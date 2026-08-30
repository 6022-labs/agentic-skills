# Grok Bot Integration

> Field notes from live interop testing — this is the framework with the
> most concrete, tested detail so far; treat OpenClaw/Hermes sections as
> less battle-tested until confirmed live.

## Shape of the problem

Grok bot's own runtime lives outside 6022. Its ENS `url` record typically
points at a **static origin** (a hosted page serving `/.well-known/*` as
plain files) — that origin cannot itself accept a live `POST /a2a`. Do not
try to make the static host "smarter"; put a small always-on HTTP process
in front instead (see
[a2a-protocol.md § exposing a live endpoint behind a static ENS origin](./a2a-protocol.md#exposing-a-live-endpoint-behind-a-static-ens-origin)).

## What Grok bot needs to stand up

1. **Static part** (can stay on the existing origin): `GET
   /.well-known/6022`, `GET /.well-known/agent-card.json`, signed with the
   mint wallet key.
2. **Live gate** (new, always-on process — Node or equivalent):
   - `POST /a2a` and `POST /responses`, both x402-gated.
   - Read the payment from `PAYMENT-SIGNATURE` then `X-PAYMENT`, and also
     accept `params.payment` in the JSON-RPC body if the origin's proxy
     drops payment headers (common on static hosts) — see
     [x402-payments.md § wire transport](./x402-payments.md#wire-transport-headers-not-just-json-body).
   - On a valid payment (or a `"0"`-price rule, signature-only), queue the
     turn and wake the actual Grok bot runtime via a **local-only**
     webhook Bearer token — this token authenticates the wake-up call, it
     is never shown to the caller and is never treated as the caller's
     identity.
   - Wait for the runtime's real answer and relay it back. Don't answer
     from the gate itself.
3. **Do not** invent a second product/endpoint to work around the static
   origin — `endpoints.responses` (or `exchange`) published on the
   existing ENS origin is enough once the gate is live.

## Failure modes seen in testing

- **Canned/stub replies**: a regex-matched "got it" or any hardcoded `200`
  from the gate process is a fail — the gate's only job is to
  authenticate + relay, the runtime must produce the actual content.
- **402 loop that never resolves**: usually the origin's proxy stripping
  `PAYMENT-SIGNATURE`/`PAYMENT-REQUIRED` headers, not a broken x402
  schema. Fix by accepting `params.payment` as a fallback (see above), not
  by disabling payment gating.
- **Inbox mistaken for A2A**: if Grok bot already has a contact form or
  chat widget on the static origin, that is not `/a2a` — don't publish it
  as `exchange`/`endpoints`, and don't register/point ENS at it until the
  real gate exists. See
  [a2a-protocol.md § inbox is not A2A](./a2a-protocol.md#inbox-is-not-a2a).

## Validation before calling it done

Run the [loop-test](./a2a-protocol.md#loop-test-verifying-a-live-a2a-integration-end-to-end)
against the same ENS origin, after a real 402 round-trip — a marker like
`PAID_V2_OK` (or a routing sanity check like `PING_PAIN_CHOCOLATINE`) must
come back verbatim from the actual runtime, not from the gate.
