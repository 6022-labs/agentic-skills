#!/usr/bin/env python3
"""Verify that a 6022 agent node is genuinely live and correctly signed.

Prints one JSON report to stdout. Exit codes are the contract:

    0  every check PASSed  -> the node is live, you may say so
    2  not proven live     -> a check FAILed, or could not be completed
    1  the verifier could not run (bad arguments, unusable input)

The distinction between 2 and 1 matters: a 2 is a result you can act on, a 1
means you learned nothing about the node.

Usage:
    python verify_node.py --origin https://agent.example.com
    python verify_node.py --origin https://agent.example.com \\
                          --ens-domain hermes.agents.80002.6022.eth \\
                          --eth-rpc https://ethereum-rpc.publicnode.com
"""

import argparse
import base64
import hashlib
import json
import secrets
import sys
import uuid

try:
    import requests
except ImportError:  # pragma: no cover - dependency guidance beats a traceback
    sys.stderr.write("missing dependency: pip install -r scripts/requirements.txt\n")
    raise SystemExit(1)

WELL_KNOWN_6022 = "/.well-known/6022"
WELL_KNOWN_CARD = "/.well-known/agent-card.json"

# Bounds what a hostile node can make us buffer and canonicalize; mirrors the
# orchestrator's RemoteAgentClient.GetWellKnownCard.
MAX_DOCUMENT_BYTES = 1024 * 1024

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Report:
    """Collects check outcomes and renders the single JSON object we print."""

    def __init__(self):
        self.checks = []

    def add(self, name, status, detail, **extra):
        entry = {"check": name, "status": status, "detail": detail}
        entry.update(extra)
        self.checks.append(entry)
        return status == PASS

    def failed(self):
        return [c for c in self.checks if c["status"] == FAIL]

    def skipped(self):
        return [c for c in self.checks if c["status"] == SKIP]

    def emit(self, origin, identity_anchored):
        live = not self.failed() and not self.skipped()
        print(json.dumps({
            "origin": origin,
            "live": live,
            # False means the signatures were only checked for self-consistency:
            # they prove the documents were not altered, not who published them.
            "identity_anchored": identity_anchored,
            "checks": self.checks,
            "failed": [c["check"] for c in self.failed()],
            "skipped": [c["check"] for c in self.skipped()],
        }, indent=2))
        return 0 if live else 2


def b64url_decode(value):
    """Decode base64url without padding, the way JOSE writes it."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jcs_canonicalize(obj):
    """RFC 8785 canonical JSON.

    Valid for the 6022 discovery documents because they contain only strings,
    booleans, arrays and objects — no floats, whose ECMAScript number formatting
    is the one part of JCS this shortcut does not implement. Key ordering is by
    code point, which matches JCS's UTF-16 ordering for the ASCII keys these
    documents use.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def address_from_public_key(pubkey_bytes):
    """Keccak-256 of the 64-byte uncompressed public key, last 20 bytes."""
    from eth_utils import keccak, to_checksum_address

    return to_checksum_address(keccak(pubkey_bytes)[-20:])


def fetch_document(url, timeout):
    """Return (raw_bytes, parsed, error). Raw bytes are kept deliberately:
    signature verification must run over exactly what arrived."""
    try:
        response = requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        return None, None, f"request failed: {exc}"

    if response.status_code != 200:
        return None, None, f"HTTP {response.status_code}"

    raw = response.raw.read(MAX_DOCUMENT_BYTES + 1, decode_content=True)
    if len(raw) > MAX_DOCUMENT_BYTES:
        return None, None, f"document larger than {MAX_DOCUMENT_BYTES} bytes"

    try:
        return raw, json.loads(raw), None
    except json.JSONDecodeError as exc:
        return raw, None, f"not valid JSON: {exc}"


def verify_one_signature(entry, canonical):
    """Verify a single detached ES256K JWS entry. Returns (ok, detail, signer)."""
    from eth_keys import KeyAPI
    from eth_keys.exceptions import BadSignature

    protected = entry.get("protected")
    signature = entry.get("signature")
    if not protected or not signature:
        return False, "entry missing protected/signature", None

    try:
        header = json.loads(b64url_decode(protected))
    except (ValueError, json.JSONDecodeError) as exc:
        return False, f"protected header is not valid base64url JSON: {exc}", None

    if header.get("alg") != "ES256K":
        return False, f"unexpected alg {header.get('alg')!r}, expected ES256K", None

    kid = header.get("kid")
    if not kid:
        return False, "protected header has no kid", None

    jwk = header.get("jwk") or {}
    try:
        pubkey_bytes = b64url_decode(jwk["x"]) + b64url_decode(jwk["y"])
    except (KeyError, ValueError) as exc:
        return False, f"protected header jwk is unusable: {exc}", kid
    if len(pubkey_bytes) != 64:
        return False, "jwk x||y is not 64 bytes", kid

    jwk_address = address_from_public_key(pubkey_bytes)
    if jwk_address.lower() != kid.lower():
        return False, f"jwk key derives {jwk_address}, but kid claims {kid}", kid

    # ES256K signs the SHA-256 of the JWS signing input. Reaching for Ethereum's
    # Keccak-256 personal_sign path here is the classic implementation error and
    # produces a signature that recovers a different address.
    signing_input = protected.encode("ascii") + b"." + b64url_encode(canonical).encode("ascii")
    digest = hashlib.sha256(signing_input).digest()

    raw_signature = b64url_decode(signature)
    if len(raw_signature) != 64:
        return False, f"signature is {len(raw_signature)} bytes, expected 64 (r||s)", kid

    # ES256K carries r||s only; try both recovery ids to find the signer.
    keys = KeyAPI()
    for recovery_id in (0, 1):
        try:
            candidate = keys.Signature(raw_signature + bytes([recovery_id]))
            recovered = candidate.recover_public_key_from_msg_hash(digest)
        except (BadSignature, ValueError):
            continue
        if address_from_public_key(recovered.to_bytes()).lower() == kid.lower():
            return True, f"valid ES256K/JCS signature by {kid}", kid

    return False, f"signature does not recover to kid {kid}", kid


def verify_signature(document, expected_address=None):
    """Verify a document's detached ES256K JWS over its JCS-canonicalized body.

    Returns (ok, detail, signer_address). The signed bytes exclude `signatures`,
    which is why it is dropped from a copy before canonicalization.

    Every entry in `signatures[]` is tried, not just the first: a node may
    publish several during a key rotation, and rejecting a document because its
    stale key happens to be listed first would take a healthy agent offline.

    `expected_address` is what makes this mean anything. Without it the check is
    self-consistency only — any key can sign a card claiming any name — so it
    proves the document was not tampered with in transit, not who published it.
    """
    signatures = document.get("signatures")
    if not signatures:
        return False, "document carries no signatures[]", None
    if not isinstance(signatures, list):
        return False, "signatures is not a list", None

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
        anchored = " (matches the address registered on-chain)" if expected_address else ""
        return True, f"valid ES256K/JCS signature by {signer}{anchored}", signer

    return False, "; ".join(failures), None


def a2a_url_from_card(card):
    """Read the A2A endpoint from the agent card's supportedInterfaces.

    It lives here and not in /.well-known/6022 — that document's `endpoints`
    object only carries `responses` and `chatCompletions`.
    """
    for interface in card.get("supportedInterfaces") or []:
        if str(interface.get("protocolBinding", "")).upper() == "JSONRPC":
            url = interface.get("url")
            if url:
                return url
    return None


def send_message(url, nonce, timeout, payment_header=None):
    """One real SendMessage turn. Returns (status_code, reply_text, error)."""
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{
                    "text": f"Reply with exactly this token and nothing else: {nonce}",
                }],
            },
        },
    }
    headers = {"Content-Type": "application/json"}
    if payment_header:
        headers["PAYMENT-SIGNATURE"] = payment_header

    try:
        response = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return None, None, f"request failed: {exc}"

    if response.status_code != 200:
        return response.status_code, None, None

    try:
        parts = response.json()["result"]["message"]["parts"]
    except (ValueError, KeyError, TypeError) as exc:
        return 200, None, f"200 with unreadable A2A result: {exc}"

    text = join_parts(parts)
    if text is None:
        return 200, None, "200 with unreadable A2A result: parts is not a list of objects"
    return 200, text, None


def join_parts(parts):
    """Concatenate the text of every part, or None if the shape is wrong.

    A peer can return syntactically valid JSON whose parts are strings rather
    than objects; calling .get() on those raises and would escape the JSON
    report and exit-code contract this script promises.
    """
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


def ens_resolver(eth_rpc, timeout):
    """Return (web3, error). Resolution is CCIP-Read (ERC-3668): the L1 resolver
    reverts with OffchainLookup and the client follows it to the 6022 ENS
    gateway, which reads from AgentEnsRegistry on the registry chain."""
    try:
        from web3 import Web3
    except ImportError:
        return None, "web3 is not installed; pip install -r scripts/requirements.txt"
    return Web3(Web3.HTTPProvider(eth_rpc, request_kwargs={"timeout": timeout})), None


def resolve_ens_url(ens_domain, eth_rpc, timeout):
    """Resolve the agent's `url` text record. Returns (value, error)."""
    w3, error = ens_resolver(eth_rpc, timeout)
    if error:
        return None, error
    try:
        return w3.ens.get_text(ens_domain, "url"), None
    except Exception as exc:  # noqa: BLE001 - any resolver failure is one outcome here
        return None, f"ENS resolution failed: {exc}"


def resolve_ens_address(ens_domain, eth_rpc, timeout):
    """Resolve the agent's registered `evm` address via ENS addr(). Returns
    (address, error).

    This is the anchor for every signature check: a document is only proof of
    identity if its signer is the address the name resolves to. Without it a
    stranger can self-sign a card for any agent's name and pass verification.
    """
    w3, error = ens_resolver(eth_rpc, timeout)
    if error:
        return None, error
    try:
        address = w3.ens.address(ens_domain)
    except Exception as exc:  # noqa: BLE001
        return None, f"ENS addr() resolution failed: {exc}"
    if not address:
        return None, f"{ens_domain} has no addr() record to anchor signatures against"
    return address, None


def normalize_origin(url):
    return url.rstrip("/")


# Ports that are implied by the scheme, so an explicit one means the same origin.
DEFAULT_PORTS = {"http": 80, "https": 443}


def same_origin(a, b):
    """Compare origins by scheme, host and effective port.

    Case and an explicitly written default port are cosmetic, so comparing raw
    netlocs reports a healthy node as misconfigured.
    """
    from urllib.parse import urlparse

    def parts(url):
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        return scheme, (parsed.hostname or "").lower(), parsed.port or DEFAULT_PORTS.get(scheme)

    return parts(a) == parts(b)


def main():
    parser = argparse.ArgumentParser(
        description="Verify a 6022 agent node is live, signed, and discoverable.",
    )
    parser.add_argument("--origin", required=True,
                        help="Public origin of the node, e.g. https://agent.example.com")
    parser.add_argument("--ens-domain",
                        help="ENS name to cross-check against the origin, e.g. hermes.agents.80002.6022.eth")
    parser.add_argument("--eth-rpc", default="https://ethereum-rpc.publicnode.com",
                        help="Ethereum RPC used for CCIP-Read ENS resolution")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    parser.add_argument("--payment-header",
                        help="A pre-signed x402 PAYMENT-SIGNATURE header, for verifying a priced node")
    parser.add_argument("--no-send-message", action="store_true",
                        help="Skip the live A2A turn (reported as an unverified check, not a pass)")
    args = parser.parse_args()

    origin = normalize_origin(args.origin)
    if not origin.startswith(("http://", "https://")):
        sys.stderr.write("--origin must be an absolute http(s) URL\n")
        return 1

    report = Report()

    # --- the anchor -------------------------------------------------------
    # Resolved first: every signature below is checked against it, so without it
    # the signature checks prove integrity but say nothing about identity.
    expected_address = None
    if args.ens_domain:
        expected_address, error = resolve_ens_address(args.ens_domain, args.eth_rpc, args.timeout)
        if error:
            report.add("identity_anchor", SKIP, error)
        else:
            report.add("identity_anchor", PASS,
                       f"{args.ens_domain} addr() -> {expected_address}; signatures must match it")

    # --- the 6022 discovery document -------------------------------------
    signer_6022 = None
    raw_6022, doc_6022, error = fetch_document(origin + WELL_KNOWN_6022, args.timeout)
    if doc_6022 is None:
        report.add("well_known_6022_reachable", FAIL,
                   f"GET {WELL_KNOWN_6022} did not return a JSON document: {error}")
    else:
        report.add("well_known_6022_reachable", PASS,
                   f"served {len(raw_6022)} bytes of JSON")
        ok, detail, signer_6022 = verify_signature(doc_6022, expected_address)
        report.add("well_known_6022_signature", PASS if ok else FAIL, detail,
                   signer=signer_6022)

    # --- the A2A agent card ----------------------------------------------
    # Verified before its URL is used: that URL is where callers send A2A traffic
    # and payment, so an unverified card can redirect both.
    signer_card = None
    raw_card, card, error = fetch_document(origin + WELL_KNOWN_CARD, args.timeout)
    if card is None:
        report.add("agent_card_reachable", FAIL,
                   f"GET {WELL_KNOWN_CARD} did not return a JSON document: {error}")
    else:
        report.add("agent_card_reachable", PASS, f"served {len(raw_card)} bytes of JSON")
        ok, detail, signer_card = verify_signature(card, expected_address)
        report.add("agent_card_signature", PASS if ok else FAIL, detail, signer=signer_card)
        if not ok:
            card = None

    # Both documents bind to the same on-chain identity, so one signer.
    if signer_6022 and signer_card:
        match = signer_6022.lower() == signer_card.lower()
        report.add("signers_match", PASS if match else FAIL,
                   f"6022 doc signed by {signer_6022}, agent card by {signer_card}")

    # --- the A2A endpoint -------------------------------------------------
    a2a_url = a2a_url_from_card(card) if card else None
    if a2a_url:
        report.add("a2a_endpoint_advertised", PASS, f"agent card advertises {a2a_url}")
    else:
        report.add("a2a_endpoint_advertised", FAIL,
                   "agent card has no JSONRPC entry in supportedInterfaces[]")

    # --- the live turn ----------------------------------------------------
    # A status code proves the route exists; only the echoed nonce proves a real
    # runtime answered rather than a gate returning a canned reply.
    if not a2a_url:
        report.add("a2a_live", SKIP, "no A2A endpoint to call")
    elif args.no_send_message:
        report.add("a2a_live", SKIP, "--no-send-message: the exchange half is unverified")
    else:
        nonce = "VERIFY-" + secrets.token_hex(6).upper()
        status, reply, error = send_message(a2a_url, nonce, args.timeout, args.payment_header)
        if error:
            report.add("a2a_live", FAIL, error)
        elif status == 402:
            report.add("a2a_live", SKIP,
                       "node is priced (402); re-run with --payment-header, or use the "
                       "call-agent-a2a skill to pay and verify")
        elif status != 200:
            report.add("a2a_live", FAIL, f"SendMessage returned HTTP {status}")
        elif reply is not None and reply.strip() == nonce:
            report.add("a2a_live", PASS, "SendMessage returned the nonce exactly: a real runtime answered")
        else:
            # Exact match, not containment: the prompt itself contains the nonce,
            # so a gate that merely reflects the request would pass a substring
            # check while no runtime ever ran. The reply is reported so a human
            # can tell a reflecting gate from a merely chatty agent.
            report.add("a2a_live", FAIL,
                       "200 but the reply was not exactly the nonce — a canned or reflected "
                       "response, or an agent that did not follow the instruction",
                       nonce=nonce, reply=(reply or "")[:200])

    # --- ENS points at this origin ---------------------------------------
    # Omitting --ens-domain is a legitimate first phase, not a skipped check:
    # the correct order is verify serving -> publish ENS -> verify again with
    # --ens-domain. Adding a SKIP here would make phase one unable to pass and
    # the ENS publish unable to start.
    if args.ens_domain:
        resolved, error = resolve_ens_url(args.ens_domain, args.eth_rpc, args.timeout)
        if error:
            report.add("ens_url_matches_origin", SKIP, error)
        elif not resolved:
            report.add("ens_url_matches_origin", FAIL,
                       f"{args.ens_domain} has no url text record")
        elif same_origin(resolved, origin):
            report.add("ens_url_matches_origin", PASS,
                       f"{args.ens_domain} url -> {resolved}")
        else:
            report.add("ens_url_matches_origin", FAIL,
                       f"{args.ens_domain} url -> {resolved}, which is not {origin}; "
                       "callers will route to the wrong host")

    return report.emit(origin, expected_address is not None)


if __name__ == "__main__":
    raise SystemExit(main())
