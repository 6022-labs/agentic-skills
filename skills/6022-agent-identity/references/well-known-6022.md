# `/.well-known/6022`

Served unauthenticated by the gateway (`gateway_mvc/core/well_known_controller.go`,
route constant `Agent6022Endpoint`). Built by
`gateway/core/use_cases/get_well_known_agent_6022.go`.

## Shape

```jsonc
{
  "protocol": "6022",
  "protocolVersion": "0.1",
  "name": "hermes",
  "ensDomain": "hermes.agents6022.eth",
  "endpoints": {
    "responses": "https://agent.example.com/api/responses",
    "chatCompletions": "https://agent.example.com/api/chat/completions" // optional, omit if unsupported
  },
  "paymentMethods": ["l402", "x402"], // sorted, distinct rule methods; omitted/empty if free
  "signatures": [
    { "protected": "<base64url JWS header>", "signature": "<base64url ES256K sig>" }
  ]
}
```

- `name` is trimmed; `ensDomain` is omitted if the agent has no ENS record
  yet.
- `endpoints` only lists the API dialects the agent actually serves — don't
  publish an endpoint you don't implement.
- `paymentMethods` is derived by listing payment rules and taking the
  distinct, sorted `Method` values. No payment rules (or a not-yet-minted
  policy) → omit the field, not an error.

## Signing (ES256K detached JWS, RFC 8812)

1. Canonicalize the JSON body with JCS (RFC 8785) — this reorders keys
   alphabetically and removes insignificant whitespace. Example canonical
   form for the payload above:
   ```json
   {"endpoints":{"responses":"https://agent.example.com/api/responses"},"ensDomain":"hermes.agents6022.eth","name":"hermes","paymentMethods":["l402","x402"],"protocol":"6022","protocolVersion":"0.1"}
   ```
2. Build a JWS protected header with alphabetically-declared fields so the
   marshalled header is already JCS-ordered:
   ```json
   {"alg":"ES256K","jwk":{"crv":"secp256k1","kty":"EC","x":"<b64url>","y":"<b64url>"},"kid":"<0x evm address>","typ":"JOSE"}
   ```
3. Sign `base64url(header) + "." + base64url(canonicalPayload)` with the
   wallet's secp256k1 key (SHA-256 digest, ES256K/RFC 8812) — this is a
   **detached** JWS: the payload itself is not embedded, only referenced by
   the canonical bytes a verifier can reproduce.
4. Publish `{"protected": base64url(header), "signature": base64url(sig)}`
   in the `signatures` array. `kid` is the agent's `evm` address, so a
   verifier can check the signature matches the minted NFT's registered
   address without any extra lookup.

A verified test vector (Anvil account 0) lives in
`agentic-agent-node/tests/agent_identity_unit_tests/services/when_signing_agent_card_test.go`
if you need to validate an implementation byte-for-byte.

## Consumer side (verifying another agent)

The orchestrator's `remote_agent_client.go` fetches this document via
`GetWellKnownCard(ctx, agentUrl)`, keeping the raw response bytes so
signature verification runs over the exact bytes received (max 1MB, to
bound what a hostile node can make you buffer/canonicalize).
