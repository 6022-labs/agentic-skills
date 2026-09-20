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
import re
import sys
import time
import unicodedata
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


# Resume anchors (pinned CIDs, tx hashes, created_at) a command registers so no failure loses them.
fail_context = {}


def fail(message, **extra):
    emit({**fail_context, **extra, "ok": False, "error": message}, code=1)


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


def load_config(path, require_images=True):
    if not path or not Path(path).exists():
        fail(f"config file not found: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"config is not valid JSON: {e}")
    if not isinstance(cfg, dict):
        fail("config must be a JSON object")
    required = ["chain_id", "name", "role", "url", "collection_address"]
    if require_images:
        required.append("default_image")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        fail(f"config missing required fields: {missing}")
    for key in ("name", "role", "url", "collection_address", "owner", "default_image"):
        if cfg.get(key) is not None and not isinstance(cfg[key], str):
            fail(f"{key} must be a string, got {cfg[key]!r}")
    if not str(cfg["chain_id"]).isdigit():
        fail(f"chain_id must be an integer, got {cfg['chain_id']!r}")
    cfg["chain_id"] = int(cfg["chain_id"])
    if cfg["role"] not in VALID_ROLES:
        fail(f"role must be one of {sorted(VALID_ROLES)}, got {cfg['role']!r}")
    for key in ("collection_address", "owner"):
        if cfg.get(key) and not Web3.is_address(cfg[key]):
            fail(f"{key} is not a valid EVM address: {cfg[key]!r}")
    if not isinstance(cfg.get("extra_wallets", []), list) or any(
        not Web3.is_address(w) for w in cfg.get("extra_wallets", [])
    ):
        fail("extra_wallets must be a list of EVM addresses")
    if not isinstance(cfg.get("extra_addresses", []), list):
        fail("extra_addresses must be a list")
    for a in cfg.get("extra_addresses", []):
        if not isinstance(a, dict) or not isinstance(a.get("type"), str) or not isinstance(a.get("value"), str) \
                or not a["value"].strip():
            fail(f'extra_addresses entries must be {{"type": "<a-z0-9>", "value": "<string>"}}, got {a!r}')
        if not re.fullmatch(r"[a-z0-9]+", a["type"]):  # AgentAddressLib rule
            fail(f"extra_addresses type {a['type']!r} must be lowercase a-z/0-9 (e.g. evm, btc, sol)")
        if a["type"] == "evm" and (not Web3.is_address(a["value"]) or int(a["value"], 16) == 0):
            fail(f"extra_addresses evm value {a['value']!r} is not a non-zero EVM address")
    if cfg.get("owner") and int(cfg["owner"], 16) == 0:
        fail("owner cannot be the zero address")
    if cfg.get("attributes", {}).get("role") not in (None, cfg["role"]):
        fail("attributes.role %r conflicts with role %r" % (cfg["attributes"]["role"], cfg["role"]))
    if cfg.get("clone_of") is not None and not str(cfg["clone_of"]).isdigit():
        fail(f"clone_of must be a token id (integer), got {cfg['clone_of']!r}")
    for key in ("images", "attributes", "extra_records"):
        if not isinstance(cfg.get(key, {}), dict):
            fail(f"{key} must be a JSON object")
        bad = {k: v for k, v in cfg.get(key, {}).items() if not isinstance(v, str)}
        if bad:
            fail(f"{key} values must be strings, got {bad!r}")
    # Stored as bare CIDs like agent-node; accept ipfs:// and gateway forms.
    for key, value in {"default_image": cfg.get("default_image"), **cfg.get("images", {})}.items():
        if not value:
            continue
        cid = extract_cid(value)
        if cid is None:
            fail(f"image {key!r} must be an IPFS CID (bare, ipfs://, or /ipfs/ path), got {value!r}. "
                 "Agent images are pinned to IPFS; http(s) URLs are rejected. "
                 "Run scripts/agentic_image.py build to produce the framed images + CIDs.")
        if key == "default_image":
            cfg["default_image"] = cid
        else:
            cfg["images"][key] = cid
    if cfg.get("images", {}).get("default") not in (None, cfg.get("default_image")):
        fail("images.default %r conflicts with default_image %r" % (cfg["images"]["default"], cfg.get("default_image")))
    return cfg


def resolve_label(cfg):
    """The label the mint stores as the name."""
    label = normalize_name(cfg["name"])
    if not validate_name(label):
        fail(f"name {cfg['name']!r} normalizes to {label!r} which is not a valid ENS label "
             "(need 1-63 chars of a-z/0-9/hyphen, no leading/trailing/double hyphen)")
    return label


CID_V0 = "^Qm[1-9A-HJ-NP-Za-km-z]{44}$"
CID_V1_BASE32 = "^[bB][a-zA-Z2-7]{50,}$"


def extract_cid(value):
    """Bare CID from any accepted form, or None (mirrors the bridge's ExtractCid)."""
    v = value.strip()
    if "/ipfs/" in v:
        v = v.split("/ipfs/", 1)[1]
    elif v.startswith("ipfs://"):
        v = v[len("ipfs://"):]
    elif "://" in v:
        return None
    v = re.split(r"[/?#]", v, 1)[0]
    if re.match(CID_V0, v) or re.match(CID_V1_BASE32, v):
        return v
    return None


def is_ipfs_image(value):
    return extract_cid(value) is not None


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
    # Fold accents (Zoë -> zoe) before filtering.
    s = "".join(ch for ch in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(ch))
    s = s.strip().lower()
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


def load_wallet():
    """Existing wallet only; `wallet` is the one command that creates."""
    if not wallet_path().exists():
        fail("no agent wallet at %s; run `agentic_identity.py wallet` first (or fix AGENTIC_WALLET_PATH)"
             % wallet_path())
    return load_or_create_wallet()


def load_or_create_wallet():
    path = wallet_path()
    password = os.environ.get("AGENTIC_WALLET_PASSWORD")
    created = False

    try:
        return _load_or_create_wallet(path, password)
    except (json.JSONDecodeError, KeyError, ValueError, OSError, TypeError) as e:
        fail("cannot load or create the wallet at %s: %s" % (path, e))


def _load_or_create_wallet(path, password):
    created = False
    if path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("wallet file must be a JSON object")
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
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(json.dumps(payload, indent=2))
        created = True

    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)
    return acct, "0x" + acct.key.hex().removeprefix("0x"), path, created


# --------------------------------------------------------------------------- #
# Chain plumbing
# --------------------------------------------------------------------------- #
def connect(cfg, chain):
    rpc = cfg.get("rpc_url") or (chain.get("rpc_urls") or [None])[0]
    if not isinstance(rpc, str) or not rpc.strip() or "<" in rpc:
        fail("no usable rpc_url. Set cfg.rpc_url or fill a key into deployments rpc_urls.")
    provider = Web3.HTTPProvider(rpc.strip(), request_kwargs={"timeout": 30})
    provider.cache_allowed_requests = True  # web3 v7 otherwise re-asks eth_chainId on every call
    w3 = Web3(provider)
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
    if cfg.get("chain_id") is not None and w3.eth.chain_id != int(cfg["chain_id"]):
        fail(f"RPC {rpc} serves chain {w3.eth.chain_id}, but config chain_id is {cfg['chain_id']}")
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
            prio = 0
        # Polygon rejects tips under ~25 gwei.
        prio = max(prio, w3.to_wei(30, "gwei"))
        return {"maxFeePerGas": base * 2 + prio, "maxPriorityFeePerGas": prio}
    except Exception:
        return {"gasPrice": w3.eth.gas_price}


def funding_payload(w3, chain, address, required, balance, **extra):
    """Shared shape of every gas-sufficiency answer."""
    shortfall = max(0, required - balance)
    return {
        "ok": True,
        "funded": shortfall == 0,
        "address": address,
        "native_symbol": chain.get("native_symbol", "ETH"),
        "balance_wei": balance,
        "balance_eth": float(w3.from_wei(balance, "ether")),
        "required_wei": required,
        "required_eth": float(w3.from_wei(required, "ether")),
        "shortfall_wei": shortfall,
        "shortfall_eth": float(w3.from_wei(shortfall, "ether")),
        "explorer": chain.get("explorer"),
        **extra,
    }


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
        return None, decode_revert(e), False
    except Exception as e:
        if "revert" in str(e).lower():
            return None, decode_revert(e), False
        return None, None, True


def decode_revert(err):
    """Custom-error selector -> Name(args) via the bundled ABIs."""
    text = str(err)
    data = getattr(err, "data", None)
    if isinstance(data, dict):
        data = data.get("data")
    blob = data if isinstance(data, str) else text
    m = re.search(r"0x[0-9a-fA-F]{8}", blob or "")
    if not m:
        return text
    selector = m.group(0).lower()
    for abi_name in ("AgentCollectionV1", "AgentEnsRegistry", "AgentCollectionsManager", "AgentCollectionCreatorV1"):
        for entry in load_abi(abi_name):
            if entry.get("type") != "error":
                continue
            sig = "%s(%s)" % (entry["name"], ",".join(i["type"] for i in entry["inputs"]))
            if Web3.keccak(text=sig)[:4].hex().lower().removeprefix("0x") == selector.removeprefix("0x"):
                return "%s(%s)" % (entry["name"], decode_revert_args(entry, blob, m.end()))
    return text


def decode_revert_args(entry, blob, start):
    from eth_abi import decode as abi_decode
    payload = re.match(r"[0-9a-fA-F]*", blob[start:]).group(0)
    try:
        values = abi_decode([i["type"] for i in entry["inputs"]], bytes.fromhex(payload))
    except Exception:
        return ""
    return ", ".join("%s=%s" % (i["name"], v) for i, v in zip(entry["inputs"], values))


def gas_for_broadcast(fn, sender, fallback, action):
    """Resolve a gas limit for a tx we are about to broadcast, or abort on revert."""
    gas, revert, failed = estimate_gas(fn, sender)
    if revert is not None:
        fail(f"{action} would revert on-chain and was NOT broadcast (no gas spent): {revert}",
             revert=True, reason=revert)
    return gas if gas is not None else fallback


def event_arg(contract, event, receipt, arg, tx_hash):
    """One argument of the event this receipt must contain; a lagging RPC read cannot fake it."""
    logs = getattr(contract.events, event)().process_receipt(receipt)
    if not logs:
        fail(f"transaction {tx_hash} succeeded but emitted no {event} event", tx_hash=tx_hash)
    return logs[0]["args"][arg]


def send_tx(w3, acct, priv, fn, gas_limit, extra=None, extra_context=None):
    """extra_context is merged into any failure payload (e.g. earlier tx hashes)."""
    try:
        tx = fn.build_transaction(
            {
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
                "chainId": w3.eth.chain_id,
                "gas": gas_limit,
                **fee_fields(w3),
                **(extra or {}),
            }
        )
    except Exception as e:
        fail(f"could not build the transaction (nothing broadcast): {e}", **(extra_context or {}))
    signed = w3.eth.account.sign_transaction(tx, priv)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    try:
        h = w3.eth.send_raw_transaction(raw)
    except Exception as e:
        fail(f"broadcast rejected by the RPC: {e}", **(extra_context or {}))
    tx_hash = Web3.to_hex(h)
    try:
        receipt = w3.eth.wait_for_transaction_receipt(h, timeout=300)
    except Exception as e:
        # Broadcast already happened; surface the hash.
        fail(f"transaction {tx_hash} not confirmed within 300s: {e}", tx_hash=tx_hash, pending=True,
             **(extra_context or {}))
    if receipt["status"] != 1:
        fail(f"transaction reverted: {tx_hash}", tx_hash=tx_hash, **(extra_context or {}))
    return tx_hash, receipt


# --------------------------------------------------------------------------- #
# Mint argument assembly — the exact tuple order the contract expects:
# mint(address to, string agentName, (string,string)[] addresses,
#      (string,string)[] images, (string,string)[] attributes, (bool,uint256) cloneOf)
# addresses are typed (addressType, value); at least one "evm" entry is
# mandatory (the agent wallet, always first). The contract canonicalizes evm
# values, any casing accepted. role is a MANDATORY attribute key, not an arg.
# --------------------------------------------------------------------------- #
def build_mint_addresses(cfg, agent_addr):
    """Typed addresses exactly as the mint stores them."""
    addresses = [("evm", Web3.to_checksum_address(agent_addr))]
    for w in cfg.get("extra_wallets", []):
        entry = ("evm", Web3.to_checksum_address(w))
        if entry not in addresses:
            addresses.append(entry)
    for a in cfg.get("extra_addresses", []):  # evm entries canonicalized like extra_wallets
        value = Web3.to_checksum_address(a["value"]) if a["type"] == "evm" else a["value"]
        entry = (a["type"], value)
        if entry not in addresses:
            addresses.append(entry)
    return addresses


def build_mint_args(cfg, agent_addr, name):
    to = Web3.to_checksum_address(cfg.get("owner") or agent_addr)
    addresses = build_mint_addresses(cfg, agent_addr)

    images = {**cfg.get("images", {}), "default": cfg["default_image"]}
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


PROPOSAL_SCAN_CAP = 500


def find_pending_proposal(w3, col, agent_addr, name, addresses=()):
    """(ours: (id, name) or None, foreign: (id, creator, why) or None, truncated).

    Ours = filed by this wallet. Foreign = another wallet's pending proposal that would make
    ours revert at approval time (same name, or one of our addresses).
    """
    ours_keys = {(t, v.lower() if t == "evm" else v) for t, v in addresses}
    total = col.functions.mintProposalsLength().call()
    scanned = min(total, PROPOSAL_SCAN_CAP)
    foreign = None
    for start in range(0, scanned, 50):
        idx = range(start, min(start + 50, scanned))
        props = None
        if hasattr(w3, "batch_requests"):
            try:
                with w3.batch_requests() as batch:
                    for i in idx:
                        batch.add(col.functions.mintProposal(i))
                    props = batch.execute()
            except Exception:
                props = None  # public RPCs often refuse batches
        if props is None:
            props = [col.functions.mintProposal(i).call() for i in idx]
        for prop in props:
            if prop[3] == agent_addr:
                return (prop[0], prop[1]), None, False
            if foreign is None and prop[1] == name:
                foreign = (prop[0], prop[3], "name %r" % name)
            elif foreign is None and ours_keys & {(t, v) for t, v in prop[7]}:
                foreign = (prop[0], prop[3], "one of our addresses")
    return None, foreign, total > scanned


def find_minted_anywhere(mgr, agent_addr):
    """(collection, tokenId, name) for this wallet in any 6022 collection, or None."""
    offset = 0
    while True:
        collection, token_id, name, nxt = mgr.functions.findAgentByAddress("evm", agent_addr, offset, 50).call()
        if token_id != 0:
            return collection, token_id, name
        if nxt == 0:
            return None
        offset = nxt


# --------------------------------------------------------------------------- #
# Read-only on-chain state used by preflight / mint / ens / status.
# --------------------------------------------------------------------------- #
def read_state(w3, cfg, chain, agent_addr, name):
    col, mgr, ens = contracts(w3, cfg, chain)
    collection = Web3.to_checksum_address(cfg["collection_address"])

    known = mgr.functions.isKnownCollection(collection).call()
    # An unknown collection may lack the typed-address ABI; say so instead of a raw revert.
    if not known:
        fail("collection %s is not registered with the 6022 AgentCollectionsManager on %s; "
             "use a listCollections result or create one (see references/flow.md)" % (collection, chain["name"]),
             collection=collection, collection_known=False)
    token_id = col.functions.addressToTokenId("evm", agent_addr).call()
    minted = token_id != 0
    if not minted:
        # One wallet, one identity (agent-node checks the manager too).
        other = find_minted_anywhere(mgr, agent_addr)
        if other is not None:
            fail("wallet %s already holds identity token %d (%r) in collection %s; "
                 "set collection_address to that collection instead of minting a second identity"
                 % (agent_addr, other[1], other[2], other[0]), minted_elsewhere=True,
                 collection=other[0], token_id=other[1])
    # Name must be unique within the collection — the contract reverts (UsedName)
    # otherwise. Read it up front so we fail clearly before spending any gas.
    name_taken_by = col.functions.nameToTokenId(name).call()
    name_available = name_taken_by == 0
    mod_count = col.functions.moderatorCount().call()

    # createMintProposal does not check pending proposals; avoid filing a duplicate.
    ours, foreign, proposal_scan_truncated = (
        find_pending_proposal(w3, col, agent_addr, name, build_mint_addresses(cfg, agent_addr))
        if not minted and mod_count > 0 else (None, None, False)
    )
    if foreign is not None:
        fail("pending mint proposal %d from wallet %s already claims %s; ours would revert at approval"
             % (foreign[0], foreign[1], foreign[2]), blocked_by_proposal=foreign[0])
    pending_proposal_id, pending_proposal_name = ours if ours else (None, None)

    # The contract reverts on a bound extra address or a missing clone_of.
    used_addresses = {}
    if not minted:
        for addr_type, value in build_mint_addresses(cfg, agent_addr)[1:]:
            holder = col.functions.addressToTokenId(addr_type, value).call()
            if holder != 0:
                used_addresses["%s:%s" % (addr_type, value)] = holder
    clone_of = cfg.get("clone_of")
    if clone_of is not None and not (0 < int(clone_of) < col.functions.nextTokenId().call()):
        fail("clone_of %s is not an existing token in this collection" % clone_of)
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
        "pending_proposal_id": pending_proposal_id,
        "pending_proposal_name": pending_proposal_name,
        "proposal_scan_truncated": proposal_scan_truncated,
        "used_addresses": used_addresses,
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
    emit({"ok": True, "address": acct.address, "path": str(path), "created": created,
          "encrypted": bool(json.loads(path.read_text()).get("encrypted"))})


def cmd_preflight(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, _, _, _ = load_wallet()
    name = resolve_label(cfg)

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

    if state["used_addresses"]:
        fail("these extra addresses already belong to another identity: %s" % state["used_addresses"], **state)
    emit({"ok": True, "address": acct.address, "chain": chain["name"],
          "ens_records_to_set": records, **state})


def cmd_fund_check(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, priv, _, _ = load_wallet()
    name = resolve_label(cfg)
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
    mint_action = None
    if state["used_addresses"]:
        fail("these extra addresses already belong to another identity: %s" % state["used_addresses"], **state)
    if not state["minted"] and state["proposal_scan_truncated"]:
        fail("more than %d pending proposals; cannot rule out a duplicate — ask a moderator to clear the queue"
             % PROPOSAL_SCAN_CAP, **state)
    if not state["minted"] and state["pending_proposal_id"] is None:
        args_tuple = build_mint_args(cfg, acct.address, name)
        if state["can_self_mint"]:
            fn, fb, mint_action = col.functions.mint(*args_tuple), 1_500_000, "mint"
        else:
            # Moderated: the agent pays for the proposal tx now; the moderator
            # pays for the actual mint later.
            fn, fb, mint_action = col.functions.createMintProposal(*args_tuple), 1_200_000, "createMintProposal"
        gas, revert, failed = estimate_gas(fn, acct.address)
        if revert is not None:
            fail(f"{mint_action} would revert; not a funding problem: {revert}", revert=True, reason=revert)
        mint_gas = int(gas / 1.25) if gas is not None else fb  # un-pad; headroom re-applied below
    ens_gas = 0
    if state["ens_supported_on_chain"] and not state["ens_provisioned"]:
        ens_gas = init_texts_gas(ens_records(cfg))  # cannot estimate before the mint exists

    total_gas = int((mint_gas + ens_gas) * 1.25)  # 25% headroom
    required = total_gas * price
    # required uses the 2*base+tip cap the node checks; what actually gets paid is base+tip.
    prio = fees.get("maxPriorityFeePerGas", 0)
    expected = total_gas * ((price - prio) // 2 + prio) if "maxFeePerGas" in fees else required
    payload = funding_payload(w3, chain, acct.address, required, state["balance_wei"],
                              expected_cost_eth=float(w3.from_wei(expected, "ether")),
                              estimated_mint_gas=mint_gas, estimated_ens_gas=ens_gas, gas_price_wei=price,
                              mint_action=mint_action, pending_proposal_id=state["pending_proposal_id"])
    funded = payload["funded"]
    emit(
        payload,
        code=0 if funded else 3,
    )


def cmd_mint(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, priv, _, _ = load_wallet()
    name = resolve_label(cfg)
    w3 = connect(cfg, chain)
    col, mgr, ens = contracts(w3, cfg, chain)
    state = read_state(w3, cfg, chain, acct.address, name)

    # Idempotent: already minted -> return existing token.
    if state["minted"]:
        emit({"ok": True, "minted": True, "already": True,
              "token_id": state["token_id"], "address": acct.address})

    if not state["collection_known"]:
        fail("collection not part of 6022; aborting before revert", **state)
    if state["proposal_scan_truncated"]:
        fail("more than %d pending proposals; cannot rule out a duplicate — ask a moderator to clear the queue"
             % PROPOSAL_SCAN_CAP, **state)
    if state["pending_proposal_id"] is not None and state["pending_proposal_name"] != name:
        emit({"ok": True, "minted": False, "proposal_submitted": True, "already": True,
              "proposal_id": state["pending_proposal_id"], "address": acct.address,
              "note": "pending proposal %d is for name %r, not %r; a moderator must refuse it before re-filing"
                      % (state["pending_proposal_id"], state["pending_proposal_name"], name)}, code=3)
    if state["pending_proposal_id"] is not None:
        emit({"ok": True, "minted": False, "proposal_submitted": True, "already": True,
              "proposal_id": state["pending_proposal_id"], "address": acct.address,
              "note": "a mint proposal for this wallet/name is already waiting for a moderator; "
                      "not filing another. Re-run 'status' later."})
    if state["used_addresses"]:
        fail("these extra addresses already belong to another identity: %s" % state["used_addresses"], **state)
    if not state["name_available"]:
        fail(f"name {name!r} is already taken (token {state['name_taken_by_token']}); "
             "aborting before revert (no gas spent). Choose a different name.", **state)

    args_tuple = build_mint_args(cfg, acct.address, name)

    if state["can_self_mint"]:
        fn = col.functions.mint(*args_tuple)
        gas = gas_for_broadcast(fn, acct.address, 1_500_000, "mint")
        tx_hash, receipt = send_tx(w3, acct, priv, fn, gas)
        token_id = event_arg(col, "Minted", receipt, "tokenId", tx_hash)
        emit({"ok": True, "minted": True, "token_id": token_id,
              "tx_hash": tx_hash, "owner": args_tuple[0], "address": acct.address})
    else:
        # Moderated collection: open a proposal; a moderator must approve later.
        fn = col.functions.createMintProposal(*args_tuple)
        gas = gas_for_broadcast(fn, acct.address, 1_200_000, "createMintProposal")
        tx_hash, receipt = send_tx(w3, acct, priv, fn, gas)
        emit({"ok": True, "minted": False, "proposal_submitted": True,
              "proposal_id": event_arg(col, "MintProposalCreated", receipt, "proposalId", tx_hash),
              "tx_hash": tx_hash, "address": acct.address,
              "note": "Collection is moderated. A moderator must approve via mintFromProposal "
                      "before the token exists. Re-run 'mint' / 'status' later to detect it."})


def ens_records(cfg):
    records = dict(cfg.get("extra_records", {}))
    records[REQUIRED_ENS_KEY] = cfg["url"]
    records.pop(RESERVED_ENS_KEY, None)
    return records


def string_slots(value):
    n = len(value.encode("utf-8"))
    return 1 if n < 32 else 1 + (n + 31) // 32


def init_texts_gas(records):
    """initAgentTexts cost fitted on Amoy (+~7% margin): base + per-record (event + slots)."""
    return 280_000 + sum(18_000 + 24_000 * string_slots(v) for v in records.values())


def set_text_gas(value):
    return 130_000 + 24_000 * string_slots(value)


def cmd_register_ens(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, priv, _, _ = load_wallet()
    name = resolve_label(cfg)
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

    records = ens_records(cfg)

    if not ens.functions.provisioned(node).call():
        # First-time provisioning: set ALL records atomically with initAgentTexts.
        recs = [(k, v) for k, v in records.items()]
        fn = ens.functions.initAgentTexts(collection, token_id, recs)
        gas = gas_for_broadcast(fn, acct.address, init_texts_gas(records), "initAgentTexts")
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
                gas = gas_for_broadcast(fn, acct.address, set_text_gas(v), f"setAgentText({k})")
                tx_hash, _ = send_tx(w3, acct, priv, fn, gas, extra_context={"updated": dict(changed)})
                changed[k] = tx_hash
        emit({"ok": True, "provisioned": True, "action": "refresh",
              "node": node.hex(), "updated": changed})


def cmd_status(args):
    cfg = load_config(args.config)
    chain = resolve_chain(cfg["chain_id"])
    acct, _, path, _ = load_wallet()
    name = resolve_label(cfg)
    w3 = connect(cfg, chain)
    state = read_state(w3, cfg, chain, acct.address, name)
    emit({"ok": True, "address": acct.address, "wallet_path": str(path),
          "chain": chain["name"], **state})


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        fail("bad arguments: %s" % message, usage=self.format_usage().strip())


def main():
    p = JsonArgumentParser(description="Deterministic agentic self-mint / self-ENS toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ["wallet", "preflight", "fund-check", "mint", "register-ens", "status"]:
        sp = sub.add_parser(cmd)
        if cmd != "wallet":
            sp.add_argument("--config", required=True, help="path to identity.json")
    args = p.parse_args()
    run_command({
        "wallet": cmd_wallet,
        "preflight": cmd_preflight,
        "fund-check": cmd_fund_check,
        "mint": cmd_mint,
        "register-ens": cmd_register_ens,
        "status": cmd_status,
    }[args.cmd], args)


def run_command(command, args):
    """Keep the one-JSON-object contract even for RPC/transport errors."""
    try:
        command(args)
    except SystemExit:
        raise
    except Exception as e:
        fail("unexpected %s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    main()
