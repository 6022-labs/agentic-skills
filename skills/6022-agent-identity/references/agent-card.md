# `/.well-known/agent-card.json` (A2A v1.0)

Served unauthenticated (route constant `AgentCardEndpoint`), built by
`gateway/core/use_cases/get_well_known_agent_card.go`. This is the
standard A2A v1.0 discovery card — 6022 just fills it in from the agent's
minted identity.

## Shape

```jsonc
{
  "name": "hermes",
  "description": "hermes — 6022 agent reachable over A2A",
  "supportedInterfaces": [
    { "url": "https://agent.example.com/api/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0" }
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
  "signatures": [ { "protected": "...", "signature": "..." } ]
}
```

Signed the same way as `/.well-known/6022` (see
[well-known-6022.md](./well-known-6022.md#signing-es256k-detached-jws-rfc-8812)) —
same wallet key, same detached ES256K JWS over the JCS-canonicalized body.

## When to add more skills/interfaces

- Add another entry to `supportedInterfaces` if you serve another binding
  (e.g. a REST binding in addition to JSON-RPC).
- Add another `skills` entry per distinct capability you want discoverable
  (each with its own `id`, `inputModes`, `outputModes`) — 6022's default
  node only advertises one `chat` skill, but nothing stops an external
  agent (Hermes, OpenClaw) from advertising more.
