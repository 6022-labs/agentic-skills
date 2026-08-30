# Minting through an agent-node's registration API

An alternative to the on-chain path in [`flow.md`](./flow.md), for the case where
the identity is being created **through a running agent-node** rather than by an
agent holding its own key and talking to the chain directly.

Use `scripts/agentic_identity.py` when the agent mints itself. Use this API when
a node already exists, holds its wallet in Vault, and an owner is driving the
registration from its console — it hands you the exact calldata instead of
letting you hand-encode Solidity.

## Which host you are talking to matters

The routes exist twice, and only one of them is reachable from outside:

| Host | Prefix | Reachable |
|------|--------|-----------|
| **gateway** (`gateway_mvc/identity`) | `/agent/registration…`, publicly at `/api/agent/registration…` | yes — **this is the one to call** |
| runtime (`agent_identity_mvc`) | `/internal/agent/registration…` | no — internal, gateway-to-runtime only |

Calling `/internal/…` from outside will not work. It is not a public API with an
odd name; it is the private hop the gateway makes on your behalf.

Every gateway registration route is **authenticated** — establish a session
first with `GET /api/auth/challenge` then `POST /api/auth/verify` (SIWE), and
send the resulting JWT. The wallet behind that session is the one the draft
belongs to.

## The routes

```
POST /api/agent/registration              # save the draft (multipart/form-data)
GET  /api/agent/registration              # read it back
GET  /api/agent/registration/mint-info    # mint() / createMintProposal() calldata
POST /api/agent/registration/mint/check   # reconcile mint status from chain
GET  /api/agent/registration/ens-info     # initAgentTexts calldata
POST /api/agent/registration/ens/check    # reconcile ENS publish status
POST /api/agent/registration/self         # mint + publish ENS in one call
```

## 1. Save the draft

`POST /api/agent/registration`, `multipart/form-data`:

| Field | Type | Notes |
|-------|------|-------|
| `chainId` | string | which network to mint on |
| `collectionAddress` | string | a real **collection instance**, never the `AgentCollectionV1` implementation — see [`flow.md`](./flow.md) |
| `name` | string | the agent's name; normalized to a valid ENS label |
| `cloneOf` | uint64, optional | token id to clone configuration from |
| `attributes` | JSON string | must satisfy the collection's mandatory keys — `role` among them |
| `image` | file | the base image, pinned to IPFS and stored as a CID |

There is **no `owner` field**: the NFT owner is the wallet of the authenticated
session. If a different address should own it, that address is the one that must
be connected.

## 2. Get the calldata

`GET /api/agent/registration/mint-info` returns the encoded `mint(...)` call —
or `createMintProposal(...)` when the collection is moderated — together with
whether moderation applies. Sign and broadcast it with the owner wallet.

## 3. Reconcile

`POST /api/agent/registration/mint/check` reads the chain and writes the
confirmed facts (owner, token id, creator) back onto the agent row. Poll it until
it reports minted; a broadcast transaction is not a confirmed identity.

## 4. Or do it in one call

`POST /api/agent/registration/self` — for a node that holds its own wallet key
via Vault. It mints, waits for confirmation, and publishes the ENS records
together.

Convenient, but note the ordering it implies: ENS ends up published before
anything has verified that the node actually serves its endpoints. Treat the
record as provisional and run `serve-agent-endpoints`' verifier before telling
anyone the agent is reachable.

## 5. ENS only

If the mint was done directly on-chain and only the records are missing:

```
GET  /api/agent/registration/ens-info     # calldata for initAgentTexts
POST /api/agent/registration/ens/check    # reconcile
```

## The attribute minimum

Every mint must carry at least one `evm` address — the wallet the agent will use
to sign its discovery documents and settle payments. It is enforced on-chain
(removing the last one reverts) and by the draft. `role` is likewise mandatory
and can never be cleared afterwards, so choose it deliberately: `clone`, `human`,
`expert`, or `facilitator`.
