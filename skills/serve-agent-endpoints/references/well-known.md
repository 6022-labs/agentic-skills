# `/.well-known/6022` — the 6022 discovery document

Unauthenticated `GET`, served at the **origin root** (never under the `/api`
prefix). On an agent-node it is built by
`agentic-agent-node/src/gateway/core/use_cases/get_well_known_agent_6022.go` and
routed by `gateway_mvc/core/well_known_controller.go`.

## Shape

```jsonc
{
  "protocol": "6022",
  "protocolVersion": "0.1",
  "name": "hermes",
  "ensDomain": "hermes.agents.80002.6022.eth",
  "endpoints": {
    "responses": "https://agent.example.com/api/responses",
    "chatCompletions": "https://agent.example.com/api/chat/completions"
  },
  "paymentMethods": ["x402"],
  "signatures": [
    { "protected": "<base64url JWS header>", "signature": "<base64url ES256K sig>" }
  ]
}
```

| Field | Rule |
|-------|------|
| `protocol` | always `"6022"` |
| `protocolVersion` | `"0.1"` today |
| `name` | the agent's name, trimmed |
| `ensDomain` | omitted entirely when the agent has no ENS record yet |
| `endpoints` | only the dialects actually served; both members are `omitempty` |
| `paymentMethods` | distinct, sorted method names from the agent's payment rules; omitted when there are none |
| `signatures` | attached *after* signing (see below) |

### `endpoints` does not carry the A2A URL

`Agent6022Endpoints` has exactly two members, `responses` and `chatCompletions`.
There is no `a2a` member and no `exchange` member. A caller that wants the A2A
endpoint reads `supportedInterfaces[].url` from
[`/.well-known/agent-card.json`](./agent-card-shape.md) instead — that is the
A2A-standard location for it, and duplicating it here would give two sources of
truth that can disagree.

### `paymentMethods` is derived, not declared

It is computed by listing the agent's payment rules and taking the distinct
sorted `Method` values. `x402` is the only method the runtime currently defines
(`agent_payment/models/enums/payment_method.go`), so today the field is either
absent or `["x402"]`. Do not publish a method the node cannot actually settle —
a caller will sign a payment against it and loop.

A missing or not-yet-minted payment policy yields *no* payment methods rather
than an error: an agent that has not priced itself is free, not broken.

## Signing — ES256K detached JWS (RFC 8812)

Both discovery documents use the identical procedure, implemented once in
`agent_identity/services/agent_card_signer.go`.

1. **Canonicalize the body with JCS (RFC 8785)** — sorts keys, strips
   insignificant whitespace. The `signatures` field must be *absent* at this
   point (it is `omitempty`, so a nil value drops out of the JSON). Canonical
   form of the example above:

   ```json
   {"endpoints":{"chatCompletions":"https://agent.example.com/api/chat/completions","responses":"https://agent.example.com/api/responses"},"ensDomain":"hermes.agents.80002.6022.eth","name":"hermes","paymentMethods":["x402"],"protocol":"6022","protocolVersion":"0.1"}
   ```

2. **Build the protected header.** Its fields are declared alphabetically so the
   marshalled bytes are already JCS-ordered without a second canonicalization
   pass:

   ```json
   {"alg":"ES256K","jwk":{"crv":"secp256k1","kty":"EC","x":"<b64url>","y":"<b64url>"},"kid":"<0x… evm address>","typ":"JOSE"}
   ```

   `x` and `y` are the 32-byte coordinates of the uncompressed public key
   (`0x04 || X || Y`, with the `0x04` prefix dropped), each base64url without
   padding. `kid` is the checksummed EVM address of that key.

3. **Sign** `base64url(header) + "." + base64url(canonicalPayload)`:

   ```
   digest    = SHA-256( protected + "." + base64url(canonical) )
   signature = secp256k1_sign(digest, privateKey)      # 65 bytes, r||s||v
   wire      = base64url( signature[0:64] )            # drop the recovery byte
   ```

   **SHA-256, not Keccak-256.** This is the single most common implementation
   error. Ethereum transactions and `personal_sign` commit to Keccak-256; ES256K
   commits to SHA-256. Using the wallet's `personal_sign` helper produces a
   signature that recovers a *different* address, and nothing local catches it —
   the failure appears only at a remote verifier.

   ES256K carries `r||s` only, 64 bytes. go-ethereum's `crypto.Sign` returns 65
   with a trailing recovery byte; it must be dropped before encoding.

4. **Attach** `{"protected": …, "signature": …}` to the `signatures` array and
   serve the resulting document.

Because the payload is not embedded in the JWS (that is what "detached" means), a
verifier reproduces the signed bytes by canonicalizing the document it received,
minus `signatures`.

## The pinned test vector

A byte-for-byte vector (Anvil account 0, a publicly known test key) is pinned on
**both** sides of the wire:

- signer — `agentic-agent-node/tests/agent_identity_unit_tests/services/when_signing_agent_card_test.go`
- verifier — `agentic-orchestrator`'s `when_verifying_agent_card_signature_test.go`

Both files hold the same literals, deliberately: if either side drifts, signed
cards stop verifying in production, and the paired tests fail before that ships.
This skill's `scripts/verify_node.py` reproduces the vector exactly — canonical
form, protected header, and signature.

Validate any new signer implementation against it before trusting it. Producing
the right *shape* is easy; producing the right *bytes* is what matters, and only
the vector tells you which you achieved.

## Verifying another agent's document

1. Fetch the raw bytes and **keep them** — verification runs over exactly what
   arrived, not over a re-serialization of a parsed structure. Re-serializing
   reorders or reformats and the digest stops matching.
2. Cap the response (the orchestrator's `RemoteAgentClient.GetWellKnownCard` uses
   1 MB) so a hostile node cannot make you buffer and canonicalize unbounded
   input.
3. Strip `signatures`, JCS-canonicalize, rebuild
   `protected + "." + base64url(canonical)`, SHA-256 it, and recover the public
   key from the 64-byte signature.
4. Check the recovered address equals the header's `kid`, **and** that `kid` is a
   registered `evm` address on the agent's NFT. Step 4 is what makes the
   signature mean something; without it a document is merely self-consistent.
