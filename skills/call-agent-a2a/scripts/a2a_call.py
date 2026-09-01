#!/usr/bin/env python3
"""Call a 6022 agent over A2A, paying its x402 challenge as the caller.

Mirrors the Go implementation field for field:
  agentic-orchestrator/agent-swarm/src/conversation_broker/services/
      x402_payment_payload_signer.go            (Sign / signEip3009 / signPermit2)
  agentic-orchestrator/agent-swarm/src/conversation_broker_http_remote_agent/
      services/remote_agent_completions_requester.go   (requestWithFreeCallRetry)

Prints one JSON report. Exit codes:
    0  the peer answered
    2  the call did not complete (the report names the stage)
    1  the script could not run (bad arguments, missing key)

Usage:
    python a2a_call.py --target hermes.agents.80002.6022.eth --message "hello"
    python a2a_call.py --target https://agent.example.com --message "hello" \\
                       --private-key-env AGENT_PRIVATE_KEY
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
    from eth_account import Account
    from eth_keys import KeyAPI
    from eth_utils import keccak, to_checksum_address
except ImportError:
    sys.stderr.write("missing dependency: pip install -r scripts/requirements.txt\n")
    raise SystemExit(1)

X402_VERSION = 2
SCHEME_EXACT = "exact"

# Uniswap's canonical Permit2, identical on every chain via CREATE2, and the
# x402 proxy authorized to pull the tokens. Restated from common/x402/permit2.go.
PERMIT2_CONTRACT = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
PERMIT2_DOMAIN_NAME = "Permit2"
PERMIT2_SPENDER = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001"

METHOD_EIP3009 = "eip3009"
METHOD_PERMIT2 = "permit2"

PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"

MAX_DOCUMENT_BYTES = 1024 * 1024
DEFAULT_CACHE = Path.home() / ".agentic" / "x402-challenges.json"

# Mirrors the orchestrator's X402PaymentSettings defaults.
CLOCK_DRIFT_TOLERANCE_SECONDS = 60
SIGNATURE_LIFETIME_SECONDS = 300


def fail(stage, detail, **extra):
    report = {"ok": False, "stage": stage, "error": detail}
    report.update(extra)
    print(json.dumps(report, indent=2))
    return 2


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def b64url_decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jcs_canonicalize(obj):
    """RFC 8785, valid for the discovery documents (no floats). See the
    serve-agent-endpoints skill for the full signing contract."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def address_from_public_key(pubkey_bytes):
    return to_checksum_address(keccak(pubkey_bytes)[-20:])


def fetch_json(url, timeout):
    try:
        response = requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        return None, f"request failed: {exc}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    raw = response.raw.read(MAX_DOCUMENT_BYTES + 1, decode_content=True)
    if len(raw) > MAX_DOCUMENT_BYTES:
        return None, f"document larger than {MAX_DOCUMENT_BYTES} bytes"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON: {exc}"


def verify_one_signature(entry, canonical):
    """Verify a single detached ES256K JWS entry. Returns (ok, detail, signer)."""
    protected, signature = entry.get("protected"), entry.get("signature")
    if not protected or not signature:
        return False, "entry missing protected/signature", None

    try:
        header = json.loads(b64url_decode(protected))
    except (ValueError, json.JSONDecodeError) as exc:
        return False, f"protected header unreadable: {exc}", None
    if header.get("alg") != "ES256K":
        return False, f"unexpected alg {header.get('alg')!r}", None

    kid = header.get("kid")
    jwk = header.get("jwk") or {}
    try:
        pubkey_bytes = b64url_decode(jwk["x"]) + b64url_decode(jwk["y"])
    except (KeyError, ValueError) as exc:
        return False, f"jwk unusable: {exc}", kid
    if len(pubkey_bytes) != 64 or address_from_public_key(pubkey_bytes).lower() != (kid or "").lower():
        return False, "jwk does not derive the kid it claims", kid

    signing_input = protected.encode("ascii") + b"." + b64url_encode(canonical).encode("ascii")
    digest = hashlib.sha256(signing_input).digest()
    raw_signature = b64url_decode(signature)
    if len(raw_signature) != 64:
        return False, f"signature is {len(raw_signature)} bytes, expected 64", kid

    keys = KeyAPI()
    for recovery_id in (0, 1):
        try:
            recovered = keys.Signature(raw_signature + bytes([recovery_id])).recover_public_key_from_msg_hash(digest)
        except Exception:  # noqa: BLE001 - a bad recovery id is one of two tries
            continue
        if address_from_public_key(recovered.to_bytes()).lower() == kid.lower():
            return True, f"valid ES256K/JCS signature by {kid}", kid
    return False, f"signature does not recover to kid {kid}", kid


def verify_card_signature(document, expected_address=None):
    """Detached ES256K JWS over the JCS-canonicalized document, signatures excluded.

    Every entry in `signatures[]` is tried: a node may publish several during a
    key rotation, and reading only the first would reject a valid document.

    `expected_address` is the agent's registered `evm` address. Without it this
    only proves the document is internally consistent — anyone can self-sign a
    card claiming any name — which is not enough before sending a payment.
    """
    signatures = document.get("signatures")
    if not signatures or not isinstance(signatures, list):
        return False, "document carries no signatures[]", None

    payload = {k: v for k, v in document.items() if k != "signatures"}
    canonical = jcs_canonicalize(payload)

    failures = []
    for index, entry in enumerate(signatures):
        if not isinstance(entry, dict):
            failures.append(f"[{index}] not an object")
            continue
        ok, detail, signer = verify_one_signature(entry, canonical)
        if not ok:
            failures.append(f"[{index}] {detail}")
            continue
        if expected_address and signer.lower() != expected_address.lower():
            failures.append(f"[{index}] signed by {signer}, not the registered {expected_address}")
            continue
        return True, f"valid ES256K/JCS signature by {signer}", signer

    return False, "; ".join(failures), None


def as_origin(url):
    """Return (origin, error). Only a bare origin is usable.

    The well-known paths are appended to this, so a target carrying a path would
    silently become `https://host/api/a2a/.well-known/6022` and be reported as a
    discovery failure that looks like the peer's fault.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, f"{url!r} is not an http(s) URL"
    if parsed.path.strip("/") or parsed.query or parsed.fragment:
        return None, (f"target must be an origin with no path, query or fragment — "
                      f"did you mean {parsed.scheme}://{parsed.netloc} ?")
    return f"{parsed.scheme}://{parsed.netloc}", None


def resolve_target(target, eth_rpc, timeout):
    """Return (origin, ens_domain, agent_address, error).

    An ENS target also yields the agent's registered `evm` address via addr(),
    which is the anchor every signature below is checked against. A URL target
    has no anchor: identity cannot be established, only document integrity.
    """
    if target.startswith(("http://", "https://")):
        origin, error = as_origin(target)
        return origin, None, None, error

    try:
        from web3 import Web3
    except ImportError:
        return None, None, None, "web3 is required to resolve an ENS name; pass a URL instead"
    try:
        w3 = Web3(Web3.HTTPProvider(eth_rpc, request_kwargs={"timeout": timeout}))
        url = w3.ens.get_text(target, "url")
        agent_address = w3.ens.address(target)
    except Exception as exc:  # noqa: BLE001 - any resolver failure is one outcome
        return None, None, None, f"ENS resolution failed: {exc}"
    if not url:
        return None, None, None, f"{target} has no url text record"

    origin, error = as_origin(url)
    if error:
        return None, None, None, f"{target} url record is unusable: {error}"
    return origin, target, agent_address, None


def a2a_url_from_card(card):
    """The A2A endpoint lives in the agent card, not in /.well-known/6022 —
    that document's `endpoints` object carries only responses/chatCompletions."""
    for interface in card.get("supportedInterfaces") or []:
        if str(interface.get("protocolBinding", "")).upper() == "JSONRPC":
            if interface.get("url"):
                return interface["url"]
    return None


# --------------------------------------------------------------------------
# x402 signing
# --------------------------------------------------------------------------

def parse_caip2(network):
    """`eip155:80002` -> 80002. Anything else is unsupported, not guessable."""
    if not isinstance(network, str) or not network.startswith("eip155:"):
        return None
    try:
        chain_id = int(network[len("eip155:"):])
    except ValueError:
        return None
    return chain_id if chain_id > 0 else None


def extra_string(option, key):
    """`assetTransferMethod`, `name` and `version` live inside the option's
    `extra` map, not at the top level of the option."""
    value = (option.get("extra") or {}).get(key)
    return value if isinstance(value, str) else ""


def is_supported(option):
    if option.get("scheme") != SCHEME_EXACT:
        return False
    if parse_caip2(option.get("network")) is None:
        return False
    method = extra_string(option, "assetTransferMethod")
    if method == METHOD_EIP3009:
        # Without the token's EIP-712 domain name the digest cannot be built.
        return extra_string(option, "name") != ""
    return method == METHOD_PERMIT2


def sign_eip3009(account, option, chain_id, amount):
    nonce = secrets.token_bytes(32)
    now = int(time.time())
    authorization = {
        "from": account.address,
        "to": option["payTo"],
        "value": str(amount),
        "validAfter": str(now - CLOCK_DRIFT_TOLERANCE_SECONDS),
        "validBefore": str(now + SIGNATURE_LIFETIME_SECONDS),
        "nonce": "0x" + nonce.hex(),
    }
    # The token's own EIP-712 domain — name and version come from the option's extra.
    domain = {
        "name": extra_string(option, "name"),
        "version": extra_string(option, "version"),
        "chainId": chain_id,
        "verifyingContract": to_checksum_address(option["asset"]),
    }
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ],
    }
    message = {
        "from": account.address,
        "to": to_checksum_address(option["payTo"]),
        "value": int(amount),
        "validAfter": now - CLOCK_DRIFT_TOLERANCE_SECONDS,
        "validBefore": now + SIGNATURE_LIFETIME_SECONDS,
        "nonce": nonce,
    }
    signed = Account.sign_typed_data(account.key, domain, types, message)
    return {"signature": "0x" + signed.signature.hex().removeprefix("0x"),
            "authorization": authorization}


def sign_permit2(account, option, chain_id, amount):
    nonce = secrets.token_bytes(32)
    nonce_int = int.from_bytes(nonce, "big")
    now = int(time.time())
    authorization = {
        "permitted": {"token": option["asset"], "amount": str(amount)},
        "from": account.address,
        "spender": PERMIT2_SPENDER,
        "nonce": str(nonce_int),
        "deadline": str(now + SIGNATURE_LIFETIME_SECONDS),
        "witness": {"to": option["payTo"], "validAfter": str(now - CLOCK_DRIFT_TOLERANCE_SECONDS)},
    }
    # Permit2's domain has NO version field — adding one changes the digest.
    domain = {
        "name": PERMIT2_DOMAIN_NAME,
        "chainId": chain_id,
        "verifyingContract": to_checksum_address(PERMIT2_CONTRACT),
    }
    types = {
        "PermitWitnessTransferFrom": [
            {"name": "permitted", "type": "TokenPermissions"},
            {"name": "spender", "type": "address"},
            {"name": "nonce", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "witness", "type": "Witness"},
        ],
        "TokenPermissions": [
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "Witness": [
            {"name": "to", "type": "address"},
            {"name": "validAfter", "type": "uint256"},
        ],
    }
    message = {
        "permitted": {"token": to_checksum_address(option["asset"]), "amount": int(amount)},
        "spender": to_checksum_address(PERMIT2_SPENDER),
        "nonce": nonce_int,
        "deadline": now + SIGNATURE_LIFETIME_SECONDS,
        "witness": {"to": to_checksum_address(option["payTo"]),
                    "validAfter": now - CLOCK_DRIFT_TOLERANCE_SECONDS},
    }
    signed = Account.sign_typed_data(account.key, domain, types, message)
    return {"signature": "0x" + signed.signature.hex().removeprefix("0x"),
            "permit2Authorization": authorization}


def sign_challenge(account, challenge):
    """Sign the first supported option in accepts[]. Returns (header, option, error).

    The amount signed is always the challenge's own: the callee accepts anything
    at or above its price, so a caller-supplied amount can only ever overpay.
    """
    if challenge.get("x402Version") != X402_VERSION:
        return None, None, f"unsupported x402 version {challenge.get('x402Version')}"

    for option in challenge.get("accepts") or []:
        if not is_supported(option):
            continue
        chain_id = parse_caip2(option["network"])
        amount = option.get("amount", "0")
        method = extra_string(option, "assetTransferMethod")
        if method == METHOD_EIP3009:
            evm_payload = sign_eip3009(account, option, chain_id, amount)
        else:
            evm_payload = sign_permit2(account, option, chain_id, amount)

        envelope = {
            "x402Version": X402_VERSION,
            "scheme": SCHEME_EXACT,
            "network": option["network"],
            "payload": evm_payload,
        }
        # Headers carry JSON as standard (not url-safe) base64; see common/x402/encoding.go.
        header = base64.b64encode(json.dumps(envelope).encode()).decode("ascii")
        return header, option, None

    return None, None, "no supported payment option in accepts[]"


def decode_challenge_header(value):
    try:
        return json.loads(base64.b64decode(value)), None
    except Exception as exc:  # noqa: BLE001 - malformed header is one outcome
        return None, f"PAYMENT-REQUIRED header is not valid base64 JSON: {exc}"


# --------------------------------------------------------------------------
# challenge cache
# --------------------------------------------------------------------------

def load_cache(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def store_cache(path, key, challenge):
    """Best-effort: the cache is an optimization, never a correctness dependency."""
    try:
        cache = load_cache(path)
        cache[key] = challenge
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(cache, indent=2))
    except OSError:
        pass


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

def send_message(url, message, timeout, payment_header=None):
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"text": message}],
            },
        },
    }
    headers = {"Content-Type": "application/json"}
    if payment_header:
        headers[PAYMENT_SIGNATURE_HEADER] = payment_header
    try:
        return requests.post(url, json=body, headers=headers, timeout=timeout), None
    except requests.RequestException as exc:
        return None, f"request failed: {exc}"


def reply_text(response):
    """The reply's text, or None if the peer's shape is unusable.

    Parts may legally be absent, but a peer can also return strings where
    objects belong; calling .get() on those would raise and escape the JSON
    report this script promises.
    """
    try:
        parts = response.json()["result"]["message"]["parts"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(parts, list):
        return None
    collected = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        text = part.get("text")
        if isinstance(text, str):
            collected.append(text)
    return "".join(collected)


def load_account(args):
    """Returns (account, error). None account is fine until a 402 arrives."""
    key = None
    if args.private_key_env:
        key = os.environ.get(args.private_key_env)
        if not key:
            return None, f"environment variable {args.private_key_env} is empty"
    elif args.wallet_file:
        try:
            data = json.loads(Path(args.wallet_file).expanduser().read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"could not read wallet file: {exc}"
        key = data.get("private_key") or data.get("privateKey")
        if not key:
            return None, "wallet file has no private_key"
    if not key:
        return None, None
    try:
        return Account.from_key(key), None
    except Exception as exc:  # noqa: BLE001 - malformed key is one outcome
        return None, f"invalid private key: {exc}"


def main():
    parser = argparse.ArgumentParser(description="Call a 6022 agent over A2A, paying x402 as the caller.")
    parser.add_argument("--target", required=True, help="ENS domain or origin URL of the agent to call")
    parser.add_argument("--message", required=True, help="Text of the turn to send")
    parser.add_argument("--private-key-env", help="Env var holding the payer's private key")
    parser.add_argument("--wallet-file", help="Wallet JSON holding the payer's private key")
    parser.add_argument("--eth-rpc", default="https://ethereum-rpc.publicnode.com",
                        help="Ethereum RPC used for CCIP-Read ENS resolution")
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE), help="Challenge cache path")
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write the challenge cache")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Do not verify the peer's card signatures (you are then trusting the origin)")
    parser.add_argument("--trust-origin", action="store_true",
                        help="Allow paying a peer whose identity could not be anchored on-chain "
                             "(a URL target, or a name with no addr() record)")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout in seconds")
    args = parser.parse_args()

    account, key_error = load_account(args)
    if key_error:
        sys.stderr.write(key_error + "\n")
        return 1

    report = {"target": args.target}

    # 1. resolve --------------------------------------------------------
    origin, ens_domain, agent_address, error = resolve_target(args.target, args.eth_rpc, args.timeout)
    if error:
        return fail("resolve", error)
    report["origin"] = origin
    report["identity_anchored"] = agent_address is not None
    if agent_address:
        report["agent_address"] = agent_address

    # 2. fetch + verify the 6022 card -----------------------------------
    card_6022, error = fetch_json(origin + "/.well-known/6022", args.timeout)
    if error:
        return fail("discovery", f"GET /.well-known/6022: {error}", origin=origin)
    if not args.skip_verify:
        ok, detail, signer = verify_card_signature(card_6022, agent_address)
        report["card_signer"] = signer
        if not ok:
            return fail("verify", f"peer's /.well-known/6022 did not verify: {detail}", origin=origin)
        report["card_verified"] = detail
    report["payment_methods"] = card_6022.get("paymentMethods") or []

    # 3. find the A2A endpoint ------------------------------------------
    # The card is verified before its URL is read: that URL is where the turn and
    # its payment go, so an unverified card can redirect both to a third party.
    agent_card, error = fetch_json(origin + "/.well-known/agent-card.json", args.timeout)
    if error:
        return fail("discovery", f"GET /.well-known/agent-card.json: {error}", origin=origin)
    if not args.skip_verify:
        ok, detail, card_signer = verify_card_signature(agent_card, agent_address)
        if not ok:
            return fail("verify", f"peer's agent card did not verify: {detail}", origin=origin)
        if signer and card_signer and card_signer.lower() != signer.lower():
            return fail("verify",
                        f"agent card is signed by {card_signer} but /.well-known/6022 by {signer}; "
                        "the two documents do not belong to the same identity",
                        origin=origin)
    a2a_url = a2a_url_from_card(agent_card)
    if not a2a_url:
        return fail("discovery", "agent card advertises no JSONRPC interface", origin=origin)
    report["a2a_url"] = a2a_url

    cache_key = ens_domain or origin
    cached = {} if args.no_cache else load_cache(args.cache_file).get(cache_key)

    # 4. send, pre-signing from cache when we have terms already ---------
    presigned_header = None
    if cached and account:
        presigned_header, _, sign_error = sign_challenge(account, cached)
        report["presigned"] = sign_error is None
    response, error = send_message(a2a_url, args.message, args.timeout, presigned_header)
    if error:
        return fail("call", error, a2a_url=a2a_url)

    # 5. handle the challenge -------------------------------------------
    if response.status_code == 402:
        if not account:
            return fail("payment",
                        "peer requires payment (402) but no wallet key was provided; "
                        "pass --private-key-env or --wallet-file",
                        a2a_url=a2a_url)

        # Paying is the point of no return, so it is the one step that insists on
        # a chain anchor: without it the signatures prove only that the documents
        # are self-consistent, which an impostor's are too.
        if agent_address is None and not (args.skip_verify or args.trust_origin):
            return fail("payment",
                        "refusing to pay a peer whose identity is not anchored on-chain — "
                        "call it by its ENS name so addr() can be resolved, or pass "
                        "--trust-origin to accept the risk deliberately",
                        a2a_url=a2a_url)

        challenge, error = decode_challenge_header(response.headers.get(PAYMENT_REQUIRED_HEADER, ""))
        if error:
            return fail("payment", error, a2a_url=a2a_url)

        # Signing the same authorization against the same challenge is
        # byte-identical and fails identically; retrying only hides the cause.
        if presigned_header and challenge == cached:
            return fail("payment",
                        "the pre-signed payment was rejected and the peer returned the same "
                        "challenge — retrying would fail identically. Check that a proxy is not "
                        "stripping PAYMENT-SIGNATURE, and that the wallet holds the challenge's asset.",
                        a2a_url=a2a_url, challenge=challenge)

        if not args.no_cache:
            store_cache(args.cache_file, cache_key, challenge)

        payment_header, option, error = sign_challenge(account, challenge)
        if error:
            return fail("payment", error, a2a_url=a2a_url, challenge=challenge)
        report["paid"] = {
            "network": option["network"],
            "asset": option["asset"],
            "payTo": option["payTo"],
            "amount": option.get("amount", "0"),
            "assetTransferMethod": extra_string(option, "assetTransferMethod"),
            "payer": account.address,
        }

        response, error = send_message(a2a_url, args.message, args.timeout, payment_header)
        if error:
            return fail("retry", error, a2a_url=a2a_url)

    # 6. report ----------------------------------------------------------
    if response.status_code != 200:
        return fail("call", f"peer returned HTTP {response.status_code}",
                    a2a_url=a2a_url, body=response.text[:500], **report)

    settlement = response.headers.get(PAYMENT_RESPONSE_HEADER)
    if settlement:
        decoded, _ = decode_challenge_header(settlement)
        report["settlement"] = decoded

    text = reply_text(response)
    if text is None:
        return fail("call", "200 with an unreadable A2A result",
                    a2a_url=a2a_url, body=response.text[:500], **report)

    report["ok"] = True
    report["reply"] = text
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
