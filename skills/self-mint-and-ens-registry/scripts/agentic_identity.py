#!/usr/bin/env python3
"""
agentic_identity.py — deterministic toolkit for an agent to self-mint an agent
NFT and self-register its ENS text records on the agentic contracts.

WHY THIS EXISTS
---------------
LLM agents are great at deciding *what* to do but unreliable at hand-crafting
contract calls, addresses, and calldata. Every value that must be exact —
contract addresses, ABIs, the ENS namehash walk, gas math, the mint argument
tuples — lives in this script and the bundled abis/ + references/deployments.json.
The agent's job is to gather a few human inputs and run subcommands in order;
it should NEVER reconstruct addresses or encode transactions itself.

Every subcommand prints a single JSON object to stdout and uses exit codes so
the calling agent can branch deterministically:
    0  = success / desired state already true
    3  = action needed by a human (e.g. wallet underfunded) — NOT an error
    1  = hard error (bad config, RPC failure, revert)

SUBCOMMANDS (run in this order)
    wallet        Load-or-create the agent wallet at a well-known path.
    preflight     Read-only: validate config + on-chain state, normalize name.
    fund-check    Compute gas needed for mint(+ENS); compare to balance.
    mint          Idempotent self-mint (or moderated proposal).
    register-ens  Idempotent ENS text-record provisioning / refresh.
    status        Read-only combined summary of everything.

CONFIG
    Pass --config path/to/identity.json (see references/identity.example.json).
    Secrets / paths come from env:
        AGENTIC_WALLET_PATH      where to store the keystore (default ~/.agentic/wallet.json)
        AGENTIC_WALLET_PASSWORD  if set, the wallet file is an encrypted V3 keystore
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from web3 import Web3
    from eth_account import Account
    from web3.exceptions import ContractLogicError
except ImportError:
    sys.stderr.write(
        "Missing dependencies. Run: pip install -r "
        + str(Path(__file__).with_name("requirements.txt"))
        + "\n"
    )
    sys.exit(1)

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
ABI_DIR = SKILL_ROOT / "abis"
DEPLOYMENTS = SKILL_ROOT / "references" / "deployments.json"

VALID_ROLES = {"clone", "human", "expert", "facilitator"}
RESERVED_ENS_KEY = "avatar"      # contract derives this; setting it reverts
REQUIRED_ENS_KEY = "url"         # initAgentTexts reverts without a non-empty url


# --------------------------------------------------------------------------- #
# Output helpers — one JSON object per run, deterministic exit codes.
# --------------------------------------------------------------------------- #
def emit(obj, code=0):
    print(json.dumps(obj, indent=2, sort_keys=True))
    sys.exit(code)


def fail(message, **extra):
    emit({"ok": False, "error": message, **extra}, code=1)


# --------------------------------------------------------------------------- #
# Config + deployments
# --------------------------------------------------------------------------- #
def load_deployments():
    with open(DEPLOYMENTS) as f:
        return json.load(f)["chains"]


def resolve_chain(chain_id):
    chains = load_deployments()
    key = str(chain_id)
    if key not in chains:
        fail(
            f"chain_id {chain_id} is not in references/deployments.json. "
            "Do NOT invent addresses — only deployed chains are supported.",
            supported_chains=sorted(chains.keys()),
        )
    return chains[key]


def load_config(path):
    if not path or not Path(path).exists():
        fail(f"config file not found: {path}")
    with open(path) as f:
        cfg = json.load(f)
    required = ["chain_id", "name", "role", "url", "default_image", "collection_address"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        fail(f"config missing required fields: {missing}")
    if cfg["role"] not in VALID_ROLES:
        fail(f"role must be one of {sorted(VALID_ROLES)}, got {cfg['role']!r}")
    return cfg


def load_abi(name):
    with open(ABI_DIR / f"{name}.abi.json") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# ENS-label normalization — mirrors AgentCollectionV1/ENSUtils.isValidENSName.
# Rules: lowercase a-z 0-9 and hyphen; 1..63 bytes; no leading/trailing hyphen;
# no consecutive hyphens. We normalize then VALIDATE so the mint cannot revert
# on InvalidName.
# --------------------------------------------------------------------------- #
def normalize_name(raw):
    s = raw.strip().lower()
    out = []
    for ch in s:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-":
            out.append(ch)
        elif ch in (" ", "_", ".", "/"):
            out.append("-")
        # drop anything else
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-")
    return s


def validate_name(name):
    if not (1 <= len(name) <= 63):
        return False
    if name[0] == "-" or name[-1] == "-":
        return False
    prev = ""
    for ch in name:
        if not (("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-"):
            return False
        if ch == "-" and prev == "-":
            return False
        prev = ch
    return True


# --------------------------------------------------------------------------- #
# Wallet — the "well-known place". Default ~/.agentic/wallet.json (0600).
# If AGENTIC_WALLET_PASSWORD is set, store an encrypted V3 keystore instead of
# a plaintext key. Idempotent: re-running returns the existing wallet.
# --------------------------------------------------------------------------- #
def wallet_path():
    return Path(
        os.environ.get("AGENTIC_WALLET_PATH", str(Path.home() / ".agentic" / "wallet.json"))
    ).expanduser()


def load_or_create_wallet():
    path = wallet_path()
    password = os.environ.get("AGENTIC_WALLET_PASSWORD")
    created = False

    if path.exists():
        data = json.loads(path.read_text())
        if data.get("encrypted"):
            if not password:
                fail("wallet is an encrypted keystore but AGENTIC_WALLET_PASSWORD is not set")
            priv = Account.decrypt(data["keystore"], password).hex()
        else:
            priv = data["private_key"]
        acct = Account.from_key(priv)
    else:
        acct = Account.create()
        priv = acct.key.hex()
        path.parent.mkdir(parents=True, exist_ok=True)
        if password:
            keystore = Account.encrypt(priv, password)
            payload = {"address": acct.address, "encrypted": True, "keystore": keystore}
        else:
            payload = {"address": acct.address, "encrypted": False, "private_key": priv}
        path.write_text(json.dumps(payload, indent=2))
        os.chmod(path, 0o600)
        created = True

    if not priv.startswith("0x"):
        priv = "0x" + priv
    return acct, priv, path, created


# --------------------------------------------------------------------------- #
# Chain plumbing
# --------------------------------------------------------------------------- #
def connect(cfg, chain):
    rpc = cfg.get("rpc_url") or (chain.get("rpc_urls") or [None])[0]
    if not rpc or "<" in rpc:
        fail("no usable rpc_url. Set cfg.rpc_url or fill a key into deployments rpc_urls.")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    # Polygon is PoA — inject the right middleware across web3 v6/v7.
    try:
        from web3.middleware import geth_poa_middleware as poa
        w3.middleware_onion.inject(poa, layer=0)
    except ImportError:
        try:
            from web3.middleware import ExtraDataToPOAMiddleware as poa
            w3.middleware_onion.inject(poa, layer=0)
        except Exception:
            pass
    if not w3.is_connected():
        fail(f"cannot connect to RPC {rpc}")
    return w3


def contracts(w3, cfg, chain):
    addrs = chain["contracts"]
    col = w3.eth.contract(
        address=Web3.to_checksum_address(cfg["collection_address"]),
        abi=load_abi("AgentCollectionV1"),
    )
    mgr = w3.eth.contract(
        address=Web3.to_checksum_address(addrs["AgentCollectionsManager"]),
        abi=load_abi("AgentCollectionsManager"),
    )
    ens = None
    if addrs.get("AgentEnsRegistry"):
        ens = w3.eth.contract(
            address=Web3.to_checksum_address(addrs["AgentEnsRegistry"]),
            abi=load_abi("AgentEnsRegistry"),
        )
    return col, mgr, ens


def fee_fields(w3):
    """EIP-1559 fields with a legacy fallback."""
    try:
        base = w3.eth.get_block("latest")["baseFeePerGas"]
        try:
            prio = w3.eth.max_priority_fee
        except Exception:
            prio = w3.to_wei(30, "gwei")
        return {"maxFeePerGas": base * 2 + prio, "maxPriorityFeePerGas": prio}
    except Exception:
        return {"gasPrice": w3.eth.gas_price}


def gas_price_estimate(w3, fees):
    return fees.get("maxFeePerGas") or fees.get("gasPrice")


def estimate_gas(fn, sender):
    """Estimate gas, distinguishing a business-logic REVERT from an RPC failure.

    Returns (gas, revert_reason, estimation_failed):
      - on success:        (int_gas, None, False)
      - on a revert:       (None, "<reason>", False)   <- the tx WOULD fail
      - on an RPC hiccup:  (None, None, True)           <- estimation unavailable

    A revert means the transaction is guaranteed to fail on-chain. Callers that
    broadcast MUST abort on a revert instead of falling back to a static gas
    limit and wasting gas — that was a real beta bug. The static fallback is only
    safe when estimation itself was unavailable (timeout / node quirk), not when
    the contract actively rejected the call.
    """
    try:
        return int(fn.estimate_gas({"from": sender}) * 1.25), None, False
    except ContractLogicError as e:
        return None, str(e), False
    except Exception:
        return None, None, True


def gas_for_broadcast(fn, sender, fallback, action):
    """Resolve a gas limit for a tx we are about to broadcast, or abort on revert."""
    gas, revert, failed = estimate_gas(fn, sender)
    if revert is not None:
        fail(f"{action} would revert on-chain and was NOT broadcast (no gas spent): {revert}",
             revert=True, reason=revert)
    return gas if gas is not None else fallback


def send_tx(w3, acct, priv, fn, gas_limit, extra=None):
    fees = fee_fields(w3)
    tx = fn.build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "chainId": w3.eth.chain_id,
            "gas": gas_limit,
            **fees,
            **(extra or {}),
        }
    )
    signed = w3.eth.account.sign_transaction(tx, priv)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    h = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=300)
    if receipt["status"] != 1:
        fail(f"transaction reverted: {h.hex()}", tx_hash=h.hex())
    return h.hex(), receipt


# --------------------------------------------------------------------------- #
# Mint argument assembly — the exact tuple order the contract expects:
# mint(address to, string agentName, (string,string)[] addresses,
#      (string,string)[] images, (string,string)[] attributes, (bool,uint256) cloneOf)
# addresses are typed (addressType, value); at least one "evm" entry is
# mandatory (the agent wallet, always first). The contract canonicalizes evm
# values, any casing accepted. role is a MANDATORY attribute key, not an arg.
# --------------------------------------------------------------------------- #
def build_mint_args(cfg, agent_addr, name):
    to = Web3.to_checksum_address(cfg.get("owner") or agent_addr)
    addresses = [("evm", Web3.to_checksum_address(agent_addr))]
    for w in cfg.get("extra_wallets", []):
        entry = ("evm", Web3.to_checksum_address(w))
        if entry not in addresses:
            addresses.append(entry)
    for a in cfg.get("extra_addresses", []):  # non-evm: {"type": ..., "value": ...}
        entry = (a["type"], a["value"])
        if entry not in addresses:
            addresses.append(entry)

    images = {"default": cfg["default_image"]}
    images.update(cfg.get("images", {}))
    images_tuples = [(k, v) for k, v in images.items()]

    attrs = {k: str(v) for k, v in cfg.get("attributes", {}).items()}
    attrs["role"] = cfg["role"]  # mandatory on-chain; config wins over attributes
    attrs_tuples = list(attrs.items())

    clone = cfg.get("clone_of")
    clone_tuple = (True, int(clone)) if clone else (False, 0)

    return (to, name, addresses, images_tuples, attrs_tuples, clone_tuple)


# --------------------------------------------------------------------------- #
# ENS node — replicates AgentEnsRegistry._resolveAgentNode (namehash walk):
#   chainNode      = keccak(parentNode || keccak(decimal_chainid))
#   collectionNode = keccak(chainNode  || keccak(collectionName))
#   node           = keccak(collectionNode || keccak(agentLabel))
# --------------------------------------------------------------------------- #
def agent_ens_node(w3, ens, col, agent_label):
    parent = ens.functions.parentNode().call()
    chain_label = Web3.keccak(text=str(w3.eth.chain_id))
    chain_node = Web3.keccak(parent + chain_label)
    coll_name = col.functions.name().call()
    coll_node = Web3.keccak(chain_node + Web3.keccak(text=coll_name))
    node = Web3.keccak(coll_node + Web3.keccak(text=agent_label))
    return node


# --------------------------------------------------------------------------- #
# Read-only on-chain state used by preflight / mint / ens / status.
# --------------------------------------------------------------------------- #
def read_state(w3, cfg, chain, agent_addr, name):
    col, mgr, ens = contracts(w3, cfg, chain)
    collection = Web3.to_checksum_address(cfg["collection_address"])

    known = mgr.functions.isKnownCollection(collection).call()
    token_id = col.functions.addressToTokenId("evm", agent_addr).call()
    minted = token_id != 0
    # Name must be unique within the collection — the contract reverts (UsedName)
    # otherwise. Read it up front so we fail clearly before spending any gas.
    name_taken_by = col.functions.nameToTokenId(name).call()
    name_available = name_taken_by == 0
    mod_count = col.functions.moderatorCount().call()
    is_mod = col.functions.isModerator(agent_addr).call()
    can_self_mint = (mod_count == 0) or is_mod

    ens_ready = ens is not None
    ens_provisioned = False
    if ens_ready and minted:
        label = col.functions.nameOf(token_id).call()
        node = agent_ens_node(w3, ens, col, label)
        ens_provisioned = ens.functions.provisioned(node).call()

    return {
        "collection": collection,
        "collection_known": known,
        "minted": minted,
        "token_id": token_id,
        "name_available": name_available,
        "name_taken_by_token": name_taken_by,
        "moderator_count": mod_count,
        "is_moderator": is_mod,
        "can_self_mint": can_self_mint,
        "ens_supported_on_chain": ens_ready,
        "ens_provisioned": ens_provisioned,
        "balance_wei": w3.eth.get_balance(agent_addr),
        "normalized_name": name,
    }


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def cmd_wallet(args):
    acct, _, path, created = load_or_create_wallet()
    emit({"ok": True, "address": acct.address, "path": str(path),
          "created": created, "encrypted": bool(os.environ.get("AGENTIC_WALLET_PASSWORD"))})


def cmd_preflight(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, _, _, _ = load_or_create_wallet()
    name = normalize_name(cfg["name"])
    if not validate_name(name):
        fail(f"name {cfg['name']!r} normalizes to {name!r} which is not a valid ENS label "
             "(need 1-63 chars of a-z/0-9/hyphen, no leading/trailing/double hyphen)")

    # validate ENS records the caller intends to set
    records = dict(cfg.get("extra_records", {}))
    records[REQUIRED_ENS_KEY] = cfg["url"]
    if RESERVED_ENS_KEY in records:
        fail(f"ENS key {RESERVED_ENS_KEY!r} is reserved (auto-derived on-chain); remove it")
    if not records.get(REQUIRED_ENS_KEY):
        fail("ENS record 'url' is mandatory and must be non-empty")

    w3 = connect(cfg, chain)
    state = read_state(w3, cfg, chain, acct.address, name)
    if not state["collection_known"]:
        fail("collection_address is not part of 6022 (not registered with the manager) on this "
             "chain. Mint would revert. Use a known collection or create one (see flow.md).",
             **state)
    if not state["minted"] and not state["name_available"]:
        fail(f"name {name!r} is already taken in this collection (token "
             f"{state['name_taken_by_token']}). Choose a different name; mint would revert "
             "with UsedName.", **state)

    emit({"ok": True, "address": acct.address, "chain": chain["name"],
          "ens_records_to_set": records, **state})


def cmd_fund_check(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, priv, _, _ = load_or_create_wallet()
    name = normalize_name(cfg["name"])
    w3 = connect(cfg, chain)
    col, mgr, ens = contracts(w3, cfg, chain)
    state = read_state(w3, cfg, chain, acct.address, name)

    # Don't price a mint that can't happen — surface the blocker instead of a
    # misleading "funded: true".
    if not state["minted"] and not state["name_available"]:
        fail(f"name {name!r} is already taken (token {state['name_taken_by_token']}); "
             "the mint would revert. Choose a different name before funding.", **state)

    fees = fee_fields(w3)
    price = gas_price_estimate(w3, fees)

    # Estimate gas for the steps still pending. A REVERT here means the tx would
    # fail, so we abort rather than report a funding number for an impossible tx.
    # A pure estimation failure (RPC quirk) falls back to a generous static limit.
    mint_gas = 0
    if not state["minted"]:
        args_tuple = build_mint_args(cfg, acct.address, name)
        if state["can_self_mint"]:
            fn, fb, label = col.functions.mint(*args_tuple), 1_500_000, "mint"
        else:
            # Moderated: the agent pays for the proposal tx now; the moderator
            # pays for the actual mint later.
            fn, fb, label = col.functions.createMintProposal(*args_tuple), 1_200_000, "createMintProposal"
        gas, revert, failed = estimate_gas(fn, acct.address)
        if revert is not None:
            fail(f"{label} would revert; not a funding problem: {revert}", revert=True, reason=revert)
        mint_gas = (gas / 1.25) if gas is not None else fb  # un-pad; headroom re-applied below
    ens_gas = 0
    if state["ens_supported_on_chain"] and not state["ens_provisioned"]:
        ens_gas = 400_000  # initAgentTexts; cannot estimate before mint exists

    total_gas = int((mint_gas + ens_gas) * 1.25)  # 25% headroom
    required = total_gas * price
    balance = state["balance_wei"]
    shortfall = max(0, required - balance)
    funded = shortfall == 0

    emit(
        {
            "ok": True,
            "funded": funded,
            "address": acct.address,
            "native_symbol": chain.get("native_symbol", "ETH"),
            "balance_wei": balance,
            "balance_eth": float(w3.from_wei(balance, "ether")),
            "required_wei": required,
            "required_eth": float(w3.from_wei(required, "ether")),
            "shortfall_wei": shortfall,
            "shortfall_eth": float(w3.from_wei(shortfall, "ether")),
            "estimated_mint_gas": mint_gas,
            "estimated_ens_gas": ens_gas,
            "gas_price_wei": price,
            "explorer": chain.get("explorer"),
        },
        code=0 if funded else 3,
    )


def cmd_mint(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, priv, _, _ = load_or_create_wallet()
    name = normalize_name(cfg["name"])
    w3 = connect(cfg, chain)
    col, mgr, ens = contracts(w3, cfg, chain)
    state = read_state(w3, cfg, chain, acct.address, name)

    # Idempotent: already minted -> return existing token.
    if state["minted"]:
        emit({"ok": True, "minted": True, "already": True,
              "token_id": state["token_id"], "address": acct.address})

    if not state["collection_known"]:
        fail("collection not part of 6022; aborting before revert", **state)
    if not state["name_available"]:
        fail(f"name {name!r} is already taken (token {state['name_taken_by_token']}); "
             "aborting before revert (no gas spent). Choose a different name.", **state)

    args_tuple = build_mint_args(cfg, acct.address, name)

    if state["can_self_mint"]:
        fn = col.functions.mint(*args_tuple)
        gas = gas_for_broadcast(fn, acct.address, 1_500_000, "mint")
        tx_hash, _ = send_tx(w3, acct, priv, fn, gas)
        token_id = col.functions.addressToTokenId("evm", acct.address).call()
        emit({"ok": True, "minted": True, "token_id": token_id,
              "tx_hash": tx_hash, "owner": args_tuple[0], "address": acct.address})
    else:
        # Moderated collection: open a proposal; a moderator must approve later.
        fn = col.functions.createMintProposal(*args_tuple)
        gas = gas_for_broadcast(fn, acct.address, 1_200_000, "createMintProposal")
        tx_hash, _ = send_tx(w3, acct, priv, fn, gas)
        emit({"ok": True, "minted": False, "proposal_submitted": True,
              "tx_hash": tx_hash, "address": acct.address,
              "note": "Collection is moderated. A moderator must approve via mintFromProposal "
                      "before the token exists. Re-run 'mint' / 'status' later to detect it."})


def cmd_register_ens(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, priv, _, _ = load_or_create_wallet()
    name = normalize_name(cfg["name"])
    w3 = connect(cfg, chain)
    col, mgr, ens = contracts(w3, cfg, chain)

    if ens is None:
        fail("AgentEnsRegistry is not deployed on this chain; ENS step is unavailable here",
             chain=chain["name"])

    state = read_state(w3, cfg, chain, acct.address, name)
    if not state["minted"]:
        fail("agent is not minted yet; mint before registering ENS", **state)

    token_id = state["token_id"]
    collection = state["collection"]
    label = col.functions.nameOf(token_id).call()
    node = agent_ens_node(w3, ens, col, label)

    records = dict(cfg.get("extra_records", {}))
    records[REQUIRED_ENS_KEY] = cfg["url"]
    records.pop(RESERVED_ENS_KEY, None)

    if not ens.functions.provisioned(node).call():
        # First-time provisioning: set ALL records atomically with initAgentTexts.
        recs = [(k, v) for k, v in records.items()]
        fn = ens.functions.initAgentTexts(collection, token_id, recs)
        gas = gas_for_broadcast(fn, acct.address, 500_000, "initAgentTexts")
        tx_hash, _ = send_tx(w3, acct, priv, fn, gas)
        emit({"ok": True, "provisioned": True, "action": "init",
              "node": node.hex(), "records": records, "tx_hash": tx_hash})
    else:
        # Already provisioned: only push records whose on-chain value drifted.
        changed = {}
        for k, v in records.items():
            current = ens.functions.text(node, k).call()
            if current != v:
                fn = ens.functions.setAgentText(collection, token_id, k, v)
                gas = gas_for_broadcast(fn, acct.address, 200_000, f"setAgentText({k})")
                tx_hash, _ = send_tx(w3, acct, priv, fn, gas)
                changed[k] = tx_hash
        emit({"ok": True, "provisioned": True, "action": "refresh",
              "node": node.hex(), "updated": changed})


def cmd_status(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, _, path, _ = load_or_create_wallet()
    name = normalize_name(cfg["name"])
    w3 = connect(cfg, chain)
    state = read_state(w3, cfg, chain, acct.address, name)
    emit({"ok": True, "address": acct.address, "wallet_path": str(path),
          "chain": chain["name"], **state})


def main():
    p = argparse.ArgumentParser(description="Deterministic agentic self-mint / self-ENS toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ["wallet", "preflight", "fund-check", "mint", "register-ens", "status"]:
        sp = sub.add_parser(cmd)
        if cmd != "wallet":
            sp.add_argument("--config", required=True, help="path to identity.json")
    args = p.parse_args()
    {
        "wallet": cmd_wallet,
        "preflight": cmd_preflight,
        "fund-check": cmd_fund_check,
        "mint": cmd_mint,
        "register-ens": cmd_register_ens,
        "status": cmd_status,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
