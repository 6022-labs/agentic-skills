# x402 as the payer — challenge, signature, settlement

Read this to understand what `scripts/a2a_call.py` produced, or to debug a
rejection. Do not reimplement the signing from it — the digest depends on details
that are invisible in a JSON dump.

Wire types: `agentic-agent-node/src/common/x402/`. Signer:
`agentic-orchestrator/agent-swarm/src/conversation_broker/services/x402_payment_payload_signer.go`.

## Headers

| Header | Direction | Contents |
|--------|-----------|----------|
| `PAYMENT-REQUIRED` | callee → caller, on 402 | base64 (**standard**, not url-safe) JSON of the challenge |
| `PAYMENT-SIGNATURE` | caller → callee | base64 JSON of the signed payload |
| `PAYMENT-RESPONSE` | callee → caller, on success | base64 JSON of the facilitator's settle result |

The challenge is also the 402 response body, but the header is what the Go client
reads. If a proxy drops unknown headers, a correctly-signed caller loops on 402
forever with no diagnostic — the single most common x402 failure in the field.

## The challenge

```jsonc
{
  "x402Version": 2,
  "resource": { "url": "https://agent.example.com/api/a2a", "description": "…" },
  "accepts": [
    {
      "scheme": "exact",
      "network": "eip155:80002",
      "amount": "10000",
      "asset": "0x41E9…",
      "payTo": "0xf39F…",
      "maxTimeoutSeconds": 60,
      "extra": { "assetTransferMethod": "eip3009", "name": "USDC", "version": "2" }
    }
  ],
  "error": "no_matching_payment_requirements"
}
```

**`assetTransferMethod`, `name` and `version` live inside `extra`**, not at the
top level of the option. Reading them from the wrong place silently yields an
empty method and no branch matches.

`amount` is in the asset's **atomic units** (so `"10000"` of a 6-decimal USDC is
0.01 USDC), and `network` is CAIP-2 — only `eip155:<chainId>` is supported. An
option is signable only when `scheme == "exact"`, the network parses, and either
the method is `permit2`, or it is `eip3009` **and** `extra.name` is present —
without the token's EIP-712 domain name the digest cannot be constructed. The
signer walks `accepts[]` in order and takes the first signable one.

`error` appears when the callee is re-issuing a challenge after rejecting an
attempt; it is the reason, and worth surfacing rather than discarding.

## The two transfer methods

Which one applies is the callee's decision, not yours: the runtime detects it
from the asset on-chain when the payment rule is saved. You read it and comply.

### EIP-3009 — the token's own `transferWithAuthorization`

Signed over the **asset's own EIP-712 domain**, which carries a `version` field:

```
domain = { name: extra.name, version: extra.version, chainId, verifyingContract: asset }
primaryType = TransferWithAuthorization
message = { from, to: payTo, value, validAfter, validBefore, nonce }
```

`nonce` is 32 random bytes as `bytes32`; the rest are `uint256`. `validAfter` is
`now - 60s` (clock-drift tolerance) and `validBefore` is `now + 300s` (signature
lifetime) — the orchestrator's `X402PaymentSettings` defaults, mirrored by the
script.

### Permit2 — any ERC-20, via Uniswap's canonical contract

Signed over **Permit2's fixed domain**, identical on every chain:

```
domain = { name: "Permit2", chainId, verifyingContract: 0x000000000022D473030F116dDEE9F6B43aC78BA3 }
primaryType = PermitWitnessTransferFrom
message = {
  permitted: { token: asset, amount },
  spender:  0x402085c248EeA27D92E8b30b2C58ed07f9E20001,   // the x402 proxy
  nonce, deadline,
  witness:  { to: payTo, validAfter }
}
```

**Permit2's domain has no `version` field.** Adding one — a natural instinct
after writing the EIP-3009 branch — changes the domain separator and therefore
the digest, and the recovered address is a stranger's. The callee reports
`invalid_payment_signature` and cannot tell you why.

The `witness` is what binds the final recipient into the signature: without it, a
Permit2 authorization would let the spender send the tokens anywhere. `nonce` is
a full `uint256` here (the 32 random bytes read as a big-endian integer), not a
`bytes32` as in EIP-3009.

## The signed envelope

```jsonc
{
  "x402Version": 2,
  "scheme": "exact",
  "network": "eip155:80002",
  "payload": {
    "signature": "0x…",                 // 65 bytes, r||s||v
    "authorization": { … }              // eip3009 — OR —
    "permit2Authorization": { … }       // permit2; the unused one is omitted
  }
}
```

Base64 (standard alphabet) the JSON and send it as `PAYMENT-SIGNATURE`. The
callee accepts `v` as either `0/1` or `27/28`, so both go-ethereum and
eth-account output work unchanged.

## What the callee does with it

1. Decodes the header and rejects anything that is not x402 v2.
2. Matches the payload against its own rules by network and `payTo`. Candidates
   can share those but differ in asset — and asset determines the EIP-712 domain
   — so it recovers the signer **once per candidate** rather than guessing.
3. Recovers the payer and checks it equals the authorization's `from`.
4. Checks the payer against the matched rule's `addressPatterns`.
5. Verifies with the facilitator, runs the turn, and settles **after** the
   response is delivered — which is why a failed turn must return non-2xx.

## Zero-price rules

A rule priced `"0"` is free but still **signed**: the caller signs an
authorization for zero and it is never settled on-chain. The signature is not
payment, it is *identification* — it is what establishes which wallet is calling,
so free access can be scoped to specific addresses via `addressPatterns`.

This is the only path the orchestrator currently exercises: its
`signFreeCallHeader` calls the signer with amount `"0"`. Paying a non-zero price
uses the identical signer with the option's `amount`, which is what
`scripts/a2a_call.py` does by default; pass `--amount` to override.

## Refusal reasons

The callee re-issues a 402 whose `error` names the cause:

| `error` | Meaning |
|---------|---------|
| `malformed_payment_signature` | the header is not base64 JSON, or not x402 v2 |
| `no_matching_payment_requirements` | signed for a network/`payTo` the callee does not offer |
| `invalid_payment_signature` | the digest did not recover to `from` — usually a wrong EIP-712 domain |
| `authorization_expired` | `validBefore` has passed; clock skew or too slow a retry |

A second identical challenge after a correctly-signed retry is not a reason to
try again — the inputs are unchanged, so the output will be too. Surface it.
