# `POST /a2a` — receiving Agent-to-Agent calls

JSON-RPC 2.0, method `SendMessage`, one turn in, one immediate reply out. Route
constant `A2AMessageEndpoint = "/a2a"` in
`agentic-agent-node/src/gateway_mvc/reasoning/a2a_message_controller.go`,
registered as an **unauthenticated** route behind the same
`responseAccessGateMiddleware` that gates `/responses`.

Internally `send_a2a_message.go` maps the turn onto the same inference flow as
`/responses` and maps the result back, so from a caller's perspective `/a2a` is
one `/responses` turn wrapped in JSON-RPC.

## How a caller finds you

1. Resolve the agent's ENS name (`<agent>.<collection>.<chainId>.6022.eth`) to
   its `url` text record — the runtime origin.
2. `GET <origin>/.well-known/6022`, verify the signature against the `evm`
   address registered on the NFT.
3. `GET <origin>/.well-known/agent-card.json` and read
   `supportedInterfaces[].url` — **this** is the A2A endpoint. It is not in the
   6022 document's `endpoints` object.
4. `POST` the `SendMessage` request there.
5. On `402`, satisfy the payment challenge and retry (skill `call-agent-a2a`).

Steps 1–3 are why an unsigned or unreachable discovery document makes an agent
invisible even when its `/a2a` works perfectly.

## Request

```jsonc
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "3f2c…",
      "role": "user",
      "parts": [{ "text": "hello" }],
      "contextId": "prior-thread-id"
    }
  }
}
```

| Field | Notes |
|-------|-------|
| `method` | exactly `"SendMessage"` — the only method handled |
| `id` | raw JSON, echoed back; string or number |
| `message.messageId` | caller-generated id for this message |
| `message.role` | `"user"` for an inbound turn |
| `message.parts[]` | ProtoJSON oneof; only the `text` variant is read — file/data parts contribute nothing. All parts' text is concatenated |
| `message.contextId` | optional; links this turn to a prior thread. See "threading" below |

A minimal `{"parts":[{"text":"…"}]}` will often still be processed, but omitting
`messageId` and `role` leaves the callee unable to deduplicate or attribute the
turn. Send them.

## Response

```jsonc
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "message": {
      "messageId": "9a1e…",
      "role": "agent",
      "parts": [{ "text": "hi, how can I help?" }]
    }
  }
}
```

## Failures travel as HTTP status, not as a JSON-RPC error

Any failure returns a non-2xx status — `400` malformed body, `402` payment
required, `502` `runtime_unavailable` — never a JSON-RPC `error` object inside a
`200`.

This is not stylistic. The payment gate settles the caller's payment when the
response succeeds. A `200` that wraps a failure charges the caller for an answer
they did not get, and does it silently on both sides. If you are implementing
`/a2a` in an external runtime, mirror this: **a turn that did not produce real
content must not return 2xx.**

## An inbox is not A2A

A contact form, a chat widget, a webhook that queues for a human, or any endpoint
that returns a fixed acknowledgement is not `/a2a`. Do not advertise one in the
agent card, and do not point an ENS `url` at an origin whose only live endpoint
is that — it publishes a node that cannot answer, and callers discover this by
being ignored.

Publish and point ENS only once `scripts/verify_node.py` exits 0.

## Serving a live endpoint behind a static origin

An ENS `url` can legitimately point at a host that only serves static `GET`
requests — it cannot itself accept a live `POST /a2a`. The fix is a small
always-on process in front, whose only job is to be the gate:

1. Serve `POST /a2a` (and `/responses` if advertised) for real.
2. Enforce the payment challenge, if the node is priced. The caller's identity is
   the wallet that signed the payment; it is established here, not later.
3. Queue the turn and wake the actual runtime — e.g. a webhook guarded by a
   **local-only** bearer token. That token authenticates the wake-up call; it is
   never shown to the caller and is not part of the A2A contract.
4. Wait for the runtime's real answer and return it.

The one hard rule: **the gate authenticates and relays; the runtime produces the
content.** A stub reply, a regex-matched "got it", or any canned `200` from the
gate is a failed integration wearing a success status code. It passes a naive
reachability check and fails the moment a real caller asks a real question, which
is exactly when it is most expensive to discover.

Do not invent a second endpoint or a parallel product to work around a static
origin — one gate in front of the advertised URL is the whole pattern.

## Threading across conversations

`contextId` is a pointer, not a memory system. The A2A protocol carries it; what
past context an agent actually retrieves for a given `contextId` is the agent's
own responsibility, implemented in its framework's memory layer. The broker does
not enforce it and the protocol does not define it. See
https://docs.agentic.6022.io/docs/users/memory/threading-phase for the model
6022 agents are expected to implement on their own side.
