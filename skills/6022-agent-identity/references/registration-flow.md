# Registration Flow (agent-node API)

The 6022 agent-node exposes an internal registration API (proxied by the
gateway, authenticated with the owner's wallet session). It drafts the mint,
gets you the exact calldata to sign, submits it, then publishes ENS — all
without you hand-encoding Solidity calls.

```
POST /internal/agent/registration
GET  /internal/agent/registration
GET  /internal/agent/registration/mint-info
POST /internal/agent/registration/mint/check
GET  /internal/agent/registration/ens-info
POST /internal/agent/registration/ens/check
POST /internal/agent/registration/self
```

## 1. Save the draft

`POST /internal/agent/registration` (multipart/form-data):

| Field | Type | Notes |
|---|---|---|
| `collectionAddress` | string | target `AgentCollectionV1` |
| `owner` | string | wallet address that will own the NFT |
| `name` | string | agent name |
| `cloneOf` | int (optional) | token id to clone config from |
| `attributes` | JSON string | must satisfy the collection's mandatory attribute keys |
| `image` | file | base avatar image |

Response: `responses.AgentRegistrationResponse` (stored draft + status).
`GET` the same endpoint to retrieve/prefill it later.

## 2. Get mint calldata

`GET /internal/agent/registration/mint-info` → `responses.MintInfoResponse`
with the encoded `mint(...)` (or `createMintProposal(...)`) calldata and
whether the collection is moderated. Sign and broadcast it with the owner
wallet, or let `self` (step 4) do it for you if the node holds the key.

## 3. Reconcile mint status

`POST /internal/agent/registration/mint/check` polls the chain and writes
confirmed facts (owner, tokenId, creator) back into the agent row. Repeat
until it reports minted.

## 4. One-call self mint + ENS publish

`POST /internal/agent/registration/self` — for nodes that hold their own
wallet key (via Vault): mints, waits for confirmation, and publishes the
`url`/`avatar` ENS text records in one call. This is what
`services/mint_publisher.go`'s `SelfMint()` orchestrates internally.

## 5. ENS-only path (if you minted directly on-chain)

If you called `mint()` yourself (see [contracts.md](./contracts.md)) and
only need ENS published:

```
GET  /internal/agent/registration/ens-info    # calldata for initAgentTexts
POST /internal/agent/registration/ens/check   # reconcile ENS publish status
```

## Minimum viable attribute set

Every mint must include at least one `evm` address pointing at the wallet
the agent will use to sign discovery documents and settle payments later —
this is validated both on-chain (`MissingEvmAddress` revert) and by the
registration draft.
