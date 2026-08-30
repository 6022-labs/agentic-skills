# x402 Wallet-to-Wallet Payments

x402 ("HTTP 402 Payment Required") gates `/responses` and `/a2a`. Payment
terms are configured per agent as `PaymentRule`s; each rule's `Method` is
what shows up in the agent's `/.well-known/6022` `paymentMethods` list.

## Rule config (`payment_rule_x402_configs`, wire shape)

```jsonc
{
  "network": "eip155:80002",       // CAIP-2: eip155:<chainId>
  "asset": "0xERC20...",           // ERC-20 contract address, validated on-chain
  "payTo": "0xReceiver...",        // payment receiver (usually the agent's own wallet)
  "assetTransferMethod": "eip3009", // or "permit2" — detected from the asset, never operator input
  "eip712Name": "USDC",            // present for eip3009 (the token's own EIP-712 domain)
  "eip712Version": "2",
  "addressPatterns": ["*"]          // which callers/paths this rule applies to
}
```

- `network` must resolve (via `ParseCaip2`) to a chain the node has an RPC
  for (`evmChainsSettings`) — payments can't be validated against a chain
  the node can't read.
- `assetTransferMethod` is one of:
  - `eip3009` — the ERC-20 itself implements `transferWithAuthorization`;
    the payer signs over **the token's own** EIP-712 domain
    (`eip712Name`/`eip712Version`).
  - `permit2` — routes through the canonical Permit2 contract; the payer
    signs over **Permit2's fixed** EIP-712 domain instead (no
    `eip712Name`/`eip712Version` needed).
- A rule `Price` of `"0"` is free: the caller still gets a signed payment
  context but nothing settles on-chain.

## Payer flow

1. Call `/responses` or `/a2a` without payment → if a rule applies, get
   back HTTP `402` with the challenge (network, asset, payTo, transfer
   method, amount).
2. Build and sign the transfer authorization (EIP-3009
   `transferWithAuthorization` or a Permit2 permit) with the payer agent's
   own wallet key — this needs no gas from the payer when using either
   meta-transfer scheme.
3. Retry the same request with the signed payment attached (per the x402
   spec's payment header).
4. The 6022 node's payment facilitator settles the transfer on-chain once
   the response is delivered; free (`"0"`) rules never trigger a
   settlement.

## Facilitator config

Per-network facilitator endpoints/timeouts live in
`runtime_common/settings/payment_facilitators_settings.go`
(`PaymentFacilitatorsSettings`) — settlement can be slow (real on-chain
tx), so the facilitator call uses a long timeout independent of the
request/response cycle.

## Wire transport (headers, not just JSON body)

> Field notes from live interop testing against `@x402/fetch` /
> `@x402/evm` clients — validate against the exact facilitator/library
> version you integrate with, this isn't from the Go backend source.

- The 402 challenge must be present **both** in the JSON body and in an
  HTTP response header `PAYMENT-REQUIRED` (the same challenge JSON,
  base64-encoded). `@x402/fetch` v2 reads the header — a body-only 402 is
  invisible to it.
- On retry, read the caller's signed payment from headers in this order:
  `PAYMENT-SIGNATURE` first, then `X-PAYMENT` (v1 compat) if absent.
- CORS must both **allow** and **expose**:
  `PAYMENT-SIGNATURE`, `PAYMENT-REQUIRED`, `PAYMENT-RESPONSE`,
  `X-PAYMENT`, `X-PAYMENT-RESPONSE` — a client behind a browser/CORS
  context can't read a header the server doesn't expose, even if it sent
  the request correctly.
- `accepts` in the challenge should list **at least two offers** so both
  v2 and v1-style clients can pick one:
  - v2: `{ "network": "eip155:80002", "amount": "0" }`
  - v1 compat: `{ "network": "polygon-amoy", "maxAmountRequired": "0" }`

If a proxy in front of the origin only forwards `Content-Type`/`Accept`
(common for static ENS-hosted origins), `PAYMENT-SIGNATURE` never reaches
the callee. Accept the same payload in `params.payment` (JSON-RPC body)
and optionally as a `?PAYMENT-SIGNATURE=` query param, in addition to the
header. A signed client stuck in a 402 loop despite retrying correctly is
usually this proxy gap, not a broken x402 schema — otherwise don't serve a
live `POST` behind that proxy at all.

## Zero-price (`"0"`) rules — what "free" actually means

A `"0"` rule still requires a **valid signed payment** (e.g. Permit2
authorization) to prove caller identity; it's the on-chain **settlement**
that's skipped, not the signature check. Flow: verify the signature →
skip settlement → route `SendMessage`/`message/send` to the runtime.

## Error responses (refusal reasons)

Reject with a `error` field naming the exact cause, not a generic
failure — this is what makes a stuck integration debuggable:
`header absent`, `header not parsed`, `invalid base64`,
`network mismatch`, `asset mismatch`, `payTo mismatch`,
`amount mismatch`, `permit2Authorization unsupported`,
`from not authorized`, `validBefore expired`, `invalid signature`.

An unrecognized/unauthorized wallet gets **HTTP 403**, not another 402
identical to the original challenge — repeating the same 402 forever for
an unknown wallet looks like a hang to the caller.

## Caller identity

The caller's identity is **always** the wallet address recovered from the
signed payment (the `from` that produced a valid Permit2/EIP-3009
signature) — never a `metadata.from` field or any other unsigned claim in
the request body. A local wake-up Bearer token used to nudge a runtime
awake (see [a2a-protocol.md](./a2a-protocol.md#exposing-a-live-endpoint-behind-a-static-ens-origin))
is transport plumbing, not an A2A identity.
