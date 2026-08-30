# `/.well-known/agent-card.json` — the A2A v1.0 discovery card

Unauthenticated `GET` at the origin root. This is the standard A2A v1.0 card;
6022 fills it in from the minted identity rather than defining its own format, so
non-6022 A2A clients can consume it unchanged. Built by
`agentic-agent-node/src/gateway/core/use_cases/get_well_known_agent_card.go`.

**This document, not `/.well-known/6022`, is where the A2A endpoint URL lives.**

## Shape

```jsonc
{
  "name": "hermes",
  "description": "hermes — 6022 agent reachable over A2A",
  "supportedInterfaces": [
    {
      "url": "https://agent.example.com/api/a2a",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "version": "1.0.0",
  "capabilities": { "streaming": false, "pushNotifications": false },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "chat",
      "name": "hermes",
      "description": "Conversational inference turn",
      "tags": ["chat"],
      "inputModes": ["text/plain"],
      "outputModes": ["text/plain"]
    }
  ],
  "signatures": [{ "protected": "…", "signature": "…" }]
}
```

Every field except `signatures`, `inputModes` and `outputModes` is required and
non-omitempty — an absent `skills` or `capabilities` makes the card invalid to
standard A2A clients even though 6022's own verifier might tolerate it.

`capabilities.streaming` and `capabilities.pushNotifications` are both `false` on
a stock agent-node: `/a2a` is one request, one immediate reply. Advertise `true`
only if the runtime genuinely implements the corresponding A2A method — a caller
will use it.

`supportedInterfaces[].url` is absolute, built as `RUNTIME_URL + PUBLIC_PREFIX +
"/a2a"`. On the default nginx layout that is `https://<origin>/api/a2a`, while
this card itself is served at `https://<origin>/.well-known/agent-card.json`. The
asymmetry is deliberate and correct; see the URL-layout table in `SKILL.md`.

## Signing

Identical to `/.well-known/6022` — same wallet key, same detached ES256K JWS over
the JCS-canonicalized body with `signatures` absent from the signed bytes. The
procedure and its two classic pitfalls are in
[`well-known.md`](./well-known.md#signing--es256k-detached-jws-rfc-8812); do not
implement a second signer.

## Extending the card

- **Another binding** (e.g. a REST interface alongside JSON-RPC) → another entry
  in `supportedInterfaces` with its own `protocolBinding`/`protocolVersion`.
- **Another capability you want discoverable** → another `skills` entry with a
  distinct `id` and its own modes.

A stock 6022 node advertises exactly one `chat` skill because that is all its
runtime exposes. An external framework with genuinely separate capabilities
should advertise them — a caller picks a skill by `id`, so an honest card gets
better-targeted traffic. Advertising a skill the runtime does not implement is
the same failure as advertising an unsupported payment method: the caller finds
out by being ignored.
