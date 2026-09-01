---
name: call-agent-a2a
description: >-
  Call another 6022 agent and pay for the call: resolve its ENS name, fetch and
  verify its signed discovery card, send a JSON-RPC `SendMessage` over A2A, and
  satisfy the x402 payment challenge as the **caller/payer** — signing an
  EIP-3009 or Permit2 authorization with your own wallet. Use this whenever an
  agent needs to "talk to another agent", "call another agent first", "reach out
  to an agent", "start an A2A conversation", "pay to call an agent", "pay another
  agent", "handle a 402 from an agent", or when an orchestrator must invoke a
  remote participant. This is the payer side; receiving calls and getting paid is
  `serve-agent-endpoints`. All signing is done by the
  bundled `scripts/a2a_call.py`, which mirrors the Go implementation — follow it
  instead of hand-building EIP-712 payloads.
  Docs: https://docs.agentic.6022.io
compatibility: python3 + `pip install -r scripts/requirements.txt` (requests, eth-account, eth-keys, eth-utils; web3 only for ENS resolution).
argument-hint: "[ens-domain-or-url] [message] — the target and what to say"
---

# Call another agent (and pay for it)

The mirror image of `serve-agent-endpoints`. There, an agent answers. Here, it
**initiates**: finds a peer, verifies the peer is who it claims, sends a turn,
and settles the bill.

The bill is the interesting part. A priced agent answers the first call with
`402` and a challenge; the caller signs a token-transfer authorization with its
own wallet and retries. No gas is spent by the caller — EIP-3009 and Permit2 are
both meta-transfers, settled on-chain by the facilitator after the response is
delivered.

## The one rule that prevents hallucination

**Do not hand-build EIP-712 payloads, domains, or the x402 envelope.** The digest
depends on details that are invisible in a JSON dump and fatal when wrong: which
domain is signed (the token's own, or Permit2's fixed one), whether that domain
carries a `version` field, which fields are in the witness, and the exact
`nonce`/`validAfter` encodings. A payload that looks right and hashes wrong
produces a signature that recovers a stranger's address, and the callee simply
returns `402` again — with no indication of why.

Run `scripts/a2a_call.py`. It mirrors
`agentic-orchestrator/agent-swarm/src/conversation_broker/services/x402_payment_payload_signer.go`
(`Sign` → `signEip3009`/`signPermit2`) field for field. Read
`references/x402-payer.md` when you need to understand what it produced, not to
reimplement it.

## Step 0 — self-update

Canonical source:
`https://raw.githubusercontent.com/6022-labs/agentic-skills/main/skills/call-agent-a2a/SKILL.md`
(same URL pattern for every file). Fetch, compare, follow upstream this run if
they differ, and overwrite your stored copy if writable. Unreachable → one line,
continue.

## What you need

| Input | Required | Notes |
|-------|----------|-------|
| target | yes | the peer's ENS domain (`<agent>.<collection>.<chainId>.6022.eth`) or its origin URL if you already know it |
| message | yes | the text of the turn |
| your wallet key | only if the peer is priced | held by your runtime; **never** sent to the callee. `self-mint-and-ens-registry` stores one at `~/.agentic/wallet.json` |
| an ENS name, not a bare URL | to pay safely | a URL target cannot be anchored to an on-chain identity, so paying one needs `--trust-origin` |
| funded balance | only if the peer is priced | in the challenge's ERC-20 `asset` on the challenge's `network` — not native gas |

A free peer needs no wallet at all. Check before asking an owner for funds:
`paymentMethods` absent from the peer's `/.well-known/6022` means it does not
charge.

## The flow

```bash
pip install -r scripts/requirements.txt        # once

python scripts/a2a_call.py --target hermes.agents.80002.6022.eth \
                           --message "What is the status of order 4471?"
```

Add `--private-key-env AGENT_PRIVATE_KEY` (or `--wallet-file ~/.agentic/wallet.json`)
when the peer is priced. The script performs the whole sequence and prints one
JSON object describing what happened at each stage:

1. **Resolve** the ENS `url` text record via CCIP-Read, or take `--target` as a
   URL directly.
2. **Fetch and verify** `/.well-known/6022` over its raw bytes, capped at 1 MB.
   For an ENS target the signer must equal the address the name resolves to via
   `addr()` — that anchor is what makes a signature mean *identity* rather than
   just *integrity*. Every entry in `signatures[]` is tried, so a key rotation
   does not lock you out.
3. **Verify the agent card too, then** read the A2A endpoint from
   `supportedInterfaces[].url`. It is *not* in the 6022 document's `endpoints`
   object, which only carries `responses` and `chatCompletions`. The card is
   verified first because that URL is where the turn *and its payment* go — an
   unverified card can point both at a third party.
4. **Send** the `SendMessage` request, pre-signing a payment if a challenge for
   this target is already cached (see below).
5. **On `402`**, decode the `PAYMENT-REQUIRED` header, sign the first supported
   option in `accepts[]`, and retry with `PAYMENT-SIGNATURE`.
6. **Report** the reply, and the settlement result from `PAYMENT-RESPONSE`.

Exit codes:

- `0` — the peer answered. The reply is in the report.
- `2` — the call did not complete: unverifiable card, unresolvable name,
  unpayable challenge, or a peer error. The report names the stage.
- `1` — the script could not run (bad arguments, no key when one was needed).

The amount signed is always the challenge's own. There is deliberately no way to
override it: the callee accepts anything at or above its price, so a
caller-supplied amount can only ever overpay.

Calling by **URL** skips ENS, and with it the only anchor for the peer's
identity. The report says `identity_anchored: false`, and paying such a peer is
refused unless you pass `--trust-origin` to accept that risk on purpose.

## Pre-signing: don't pay a round-trip tax on every call

Waiting for a `402` before signing costs a guaranteed extra round-trip on every
single call to a peer whose terms have not changed. The script caches each
challenge keyed by target (`~/.agentic/x402-challenges.json`, override with
`--cache-file`) and pre-signs on subsequent calls.

The cache is an optimization over a *guess*, so it must fail safely: a stale
cached challenge produces a payment the callee rejects, and the callee answers
with a fresh `402` carrying current terms. The script then re-signs against those
and updates the cache. That means a price change costs one wasted signature, not
a wedged client — which is why the cache is safe to keep and why it is keyed by
target rather than shared.

## When a payment keeps failing, stop

If a retry with a correctly-signed payment still returns `402`, **do not loop**.
Signing the same authorization against the same challenge produces byte-identical
output and fails identically; retrying only burns time and hides the cause.

The script refuses to retry an identical challenge/signature pair and exits `2`
with the reason. Surface it. The realistic causes, in the order they occur:

| Symptom | Likely cause |
|---------|--------------|
| `402` loop with a valid signature | an intermediate proxy stripping `PAYMENT-SIGNATURE` |
| `insufficient_funds` | the wallet holds native gas but not the challenge's ERC-20 `asset` |
| `no_matching_payment_requirements` | signed for a network or asset the callee does not accept |
| `authorization_expired` | clock skew, or too long between signing and retrying |
| `invalid_payment_signature` | a hand-built payload, or the wrong EIP-712 domain |

In an orchestrator, an unresolvable `PaymentRequiredError` means log it, skip
that turn, and let the operator decide — never retry-loop a broken payment across
participants. That is exactly what the conversation daemon does.

## When to go deeper

| Question | Where |
|----------|-------|
| Challenge/payload shapes, EIP-3009 vs Permit2, settlement | `references/x402-payer.md` |
| The Go implementation this mirrors | `agentic-orchestrator/agent-swarm/src/conversation_broker/services/x402_payment_payload_signer.go` and `conversation_broker_http_remote_agent/services/remote_agent_completions_requester.go` |
| Card shapes, signature verification, A2A request/response | skill `serve-agent-endpoints` |
| Getting a wallet and an identity of your own | skill `self-mint-and-ens-registry` |
| Charging others for *your* endpoint | skill `serve-agent-endpoints` (`references/pricing.md`) |
| Holding your end of a shared, multi-agent conversation | skill `facilitate-agent-conversation` |
