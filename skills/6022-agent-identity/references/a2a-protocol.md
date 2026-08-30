# A2A Protocol (`POST /a2a`)

Route constant `A2AMessageEndpoint = "/a2a"`, JSON-RPC 2.0, method
`SendMessage`. Gated by the same x402 access middleware as `/responses`
(`responseAccessGateMiddleware`) — a failed turn never settles a payment.

## Discovery order (how an orchestrator/agent finds and calls another agent)

1. Resolve the target's ENS name (`<agent>.<collection>.<chainId>.6022.eth`)
   via the [CCIP-Read flow](./ens-ccip-read.md) to get its `url` text
   record (the runtime base URL).
2. `GET <url>/.well-known/6022` (and/or `/.well-known/agent-card.json`),
   verify the signature against the `evm` address on the minted NFT.
3. `POST <url>/a2a` with a JSON-RPC `SendMessage` request.
4. If the response is `402`, follow the [x402 flow](./x402-payments.md) and
   retry with the signed payment attached.

## Request / response

```jsonc
// request
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "SendMessage",
  "params": {
    "message": { "parts": [{ "text": "hello" }] }
  }
}

// response (immediate, single turn — no streaming)
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "message": { "parts": [{ "text": "hi, how can I help?" }] }
  }
}
```

`send_a2a_message.go` maps the incoming A2A `SendMessage` turn onto the same
internal Responses flow used by `/responses`, and maps the result back to
an A2A response — from the caller's point of view it behaves exactly like
one turn of `/responses`, just wrapped in JSON-RPC.

## Failure handling

Any error returns a non-2xx HTTP status (not a JSON-RPC error embedded in a
200) specifically so the x402 gate never settles a payment for a failed
turn.

## Inbox is not A2A

A public contact form, a chat widget, or any endpoint only a human reads is
not `/a2a`. Don't publish it as `endpoints`/`exchange` in
[well-known-6022.md](./well-known-6022.md), and don't register/point an
ENS `url` at it — that would advertise a node that can't actually reply to
`SendMessage`. Only publish `/a2a` (and register ENS pointing at it) once
the [loop-test](#loop-test-verifying-a-live-a2a-integration-end-to-end)
below passes.

## Exposing a live endpoint behind a static ENS origin

> Field notes from integrating an external framework (Grok bot) whose ENS
> `url` record points at a static origin.

An ENS `url` record can point at a static host that only serves `GET
/.well-known/*` — it cannot itself accept a live `POST /a2a`. In that case
put a small always-on HTTP process (Node or equivalent) in front, whose
only job is to be the gate:

1. Serve `POST /a2a` and `POST /responses` for real.
2. Enforce the x402 challenge/verification described in
   [x402-payments.md](./x402-payments.md) — the caller's identity is the
   wallet that signed the payment, established here, not later.
3. Queue the turn and wake the actual agent runtime (e.g. via a webhook
   guarded by a **local-only** Bearer token that is never shown to the
   caller — it authenticates the wake-up call, not the A2A request).
4. Wait for the runtime's real answer and return it — don't answer from
   the gate itself.

A stub reply, a regex-matched "got it", or any canned `200` from the gate
process is a fail: the gate's job is only to authenticate + relay, the
runtime must produce the actual content. If the origin's proxy strips
payment headers (see [x402-payments.md](./x402-payments.md#wire-transport-headers-not-just-json-body)),
the gate must also accept the payment payload via `params.payment` —
otherwise a correctly-signed caller loops on 402 forever.

Don't invent a second product/endpoint to work around this —
`endpoints.responses` published on the ENS origin is enough; run the
loop-test below against that same origin, after the 402 round-trip.

## Loop-test (verifying a live A2A integration end to end)

1. `POST` without payment → expect `402`, with the `PAYMENT-REQUIRED`
   header present (not just the JSON body — see
   [x402-payments.md](./x402-payments.md#wire-transport-headers-not-just-json-body)).
2. Retry with a signed `PAYMENT-SIGNATURE` from an accepted wallet
   (e.g. via `@x402/evm`), sending
   `params.message.parts = "Reply with exactly PAID_V2_OK."` → expect
   `200` with `result.message.parts[0].text === "PAID_V2_OK"`.
3. A canned `200`, or a `200` where the callee never actually identified
   the payer, is still a fail even if the status code looks right.

Keep a distinct marker (e.g. `PING_PAIN_CHOCOLATINE`) as a routing-content
sanity check — if it comes back unchanged, the runtime is actually being
invoked rather than short-circuited by the gate.
