---
name: 6022-a2a-initiate
description: 'Have an agent proactively start an Agent-to-Agent (A2A) conversation with another 6022 agent: resolve its ENS name, fetch its /.well-known/6022 card, send the first SendMessage call, and pay the x402 challenge as the caller/payer (not the receiver). Use when the user asks to "talk to another agent", "call another agent first", "start an A2A conversation", or "pay to call another agent".'
argument-hint: 'Target agent ENS domain or URL, and the message to send'
---

# Initiate A2A as a Caller (Payer Side)

Companion to [6022-agent-identity](../6022-agent-identity/SKILL.md), which
covers *becoming* a reachable node (mint, well-known, receiving
`SendMessage`). This skill is the other direction: proactively **calling**
another agent and paying its x402 challenge as the payer, grounded in
`agentic-orchestrator`'s remote-agent client.

## When to Use

- Your agent needs to reach out to another 6022 agent first (not just
  respond to inbound calls).
- You need to pay another agent's x402 challenge as the **caller** — the
  signer/payer role, the mirror image of
  [6022-agent-identity's x402-payments.md](../6022-agent-identity/references/x402-payments.md)
  which covers verifying/accepting payment as the receiver.

## Prerequisites

- Your own wallet's private key, held by your runtime (never sent to the
  callee).
- The target agent's **ENS domain** (`<agent>.<collection>.<chainId>.6022.eth`)
  or a known URL if you're skipping ENS resolution.
- Don't trust hardcoded addresses/routes forever — see
  [6022-agent-identity/references/keeping-current.md](../6022-agent-identity/references/keeping-current.md).
  If you operate your own node, its
  [self-check.md](../6022-agent-identity/references/self-check.md) should
  be green before you rely on it to receive replies/callbacks.

## Step 1 — Discover the target

1. Resolve the ENS domain via CCIP-Read to get its `url` text record (see
   [6022-agent-identity/references/ens-ccip-read.md](../6022-agent-identity/references/ens-ccip-read.md)).
2. `GET <url>/.well-known/6022` and verify the signature against the
   `evm` address the card claims — cap the response at ~1MB and keep the
   raw bytes around, signature verification runs over those exact bytes
   (mirrors `RemoteAgentClient.GetWellKnownCard`,
   `agentic-orchestrator/agent-swarm/src/conversation_broker_http_remote_agent/clients/remote_agent_client.go`).
3. Read `endpoints`/`exchange` for the A2A URL, and `paymentMethods` to
   know whether to expect a 402 at all.

## Step 2 — Send the first message, pre-signed if you've called before

Don't always wait for a 402 before signing — if you've talked to this
`ensDomain` before, look up any cached payment challenge for it and
pre-sign a fresh header before the first call. This avoids a guaranteed
round-trip on every call to an agent whose terms haven't changed:

```
1. cached := readChallengeCache(ensDomain)
2. if cached exists: header := signPayment(cached)   # pre-sign
3. POST <url>/a2a  { SendMessage ... }  with PAYMENT-SIGNATURE: header (if any)
```

## Step 3 — Handle the 402 challenge (you are the payer here)

If the response is `402`:

1. Read the `PAYMENT-REQUIRED` response header (base64 JSON) — this is
   the same challenge shape documented in
   [6022-agent-identity/references/x402-payments.md](../6022-agent-identity/references/x402-payments.md),
   with an `accepts[]` array of options (network as CAIP-2, asset, payTo,
   `assetTransferMethod`).
2. Cache the challenge keyed by `ensDomain` so future calls can pre-sign
   (Step 2) instead of round-tripping every time.
3. If the challenge is identical to what you already tried and failed
   with, don't retry with the same signature — it will fail identically.
   Surface the error instead of looping.
4. Sign the first supported option in `accepts[]`:
   - **EIP-3009**: build `{from: your address, to: payTo, value: amount,
     validAfter, validBefore, nonce}` and sign it as EIP-712 typed data
     over the **token's own domain** (name/version come from the
     option's `extra` field).
   - **Permit2**: build a `PermitWitnessTransferFrom` over Permit2's
     fixed domain (canonical address
     `0x000000000022D473030F116dDEE9F6B43aC78BA3`), spender = the x402
     proxy address, witness = `{to: payTo, validAfter}`.
5. Wrap the signed authorization in the x402 payload
   (`{x402Version: 2, scheme: "exact", network: <CAIP-2>, payload: {...}}`),
   base64-encode it, and retry the call with `PAYMENT-SIGNATURE: <that>`.

Track the outcome (settled / rejected / skipped) per network — you want to
notice if a given callee's chain/facilitator starts failing consistently
rather than silently eating retries.

## Step 4 — On repeated `PaymentRequiredError` failures

If paying still doesn't get through (bad signature, wrong network,
facilitator down), don't spin retrying the same request — surface the
failure. In an orchestrator context this typically means: log it, skip
that turn, and let the human/facilitator decide whether to try a
different agent.

## Reference implementation to mirror

`agentic-orchestrator/agent-swarm/src/conversation_broker_http_remote_agent/services/remote_agent_completions_requester.go`
(`RequestCompletions` → `requestWithFreeCallRetry`) and
`agentic-orchestrator/agent-swarm/src/conversation_broker/services/x402_payment_payload_signer.go`
(`Sign` → `signEip3009`/`signPermit2`) implement exactly this flow in Go —
read them directly if you need exact field names/types rather than
re-deriving them from this description.
