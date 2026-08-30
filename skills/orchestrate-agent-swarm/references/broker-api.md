# Conversation broker API

Every route of `agentic-orchestrator/agent-swarm`'s
`conversation_broker_mvc`. Bodies are the use-case request structs in
`conversation_broker/use_cases/requests/`; ids are UUIDs unless stated otherwise.

## Bridges

| Route | Body | Notes |
|-------|------|-------|
| `POST /bridges` | `{ id?, kind, baseUrl }` | Upsert. `kind` ∈ `slack`. `baseUrl` is where completions are posted back. Supplying `id` makes it idempotent across restarts; omitting it has the broker generate one. `id` may not be the zero UUID |

## Participants

| Route | Body | Notes |
|-------|------|-------|
| `POST /participants/agents` | `{ collectionAddress, collectionTokenId }` | `collectionTokenId` is a **string** holding an unsigned integer. The participant's id equals the agent-identity service's UUID for that agent |
| `POST /participants/users` | `{ displayName }` | Non-empty. The broker generates the UUID |

A participant row is either an agent (collection address **and** token id
present) or a user (display name present, on-chain fields nil) — the invariant is
enforced at the application layer. `ensDomain` is stored on the row but derived
from the on-chain identity; it is never the identifier you register with.

## Conversations

| Route | Body / params | Notes |
|-------|---------------|-------|
| `POST /conversations` | `{ bridgeId, description?, participantIds? }` | Only `bridgeId` is required. Every entry of `participantIds` must be a non-zero UUID |
| `GET /conversations` | `?page=&elementsPerPage=` | Defaults page 1, 10 per page, capped at 100 |
| `GET /conversations/{id}` | — | One conversation |
| `PATCH /conversations/{id}/description` | `{ description }` | Nullable — pass `null` to clear |
| `POST /conversations/{id}/force-conclude` | — | Operator-side conclusion, without waiting for a facilitator's marker |

A conversation carries `status`, `concludedAt` and `unconcludedAt`. Bridge-side
identifiers (a Slack channel and thread timestamp, say) are **not** stored here —
the broker holds only its own UUID, and the bridge keeps the mapping.

## Participants in a conversation

| Route | Body / params |
|-------|---------------|
| `POST /conversations/{id}/participants` | `{ participantId }` |
| `GET /conversations/{id}/participants` | — |
| `DELETE /conversations/{id}/participants/{participantId}` | — |

## Messages

| Route | Body / params | Notes |
|-------|---------------|-------|
| `POST /conversations/{id}/messages` | `{ participantId, content }` | **The only write path.** `content` must be non-empty. Called by the bridge *after* it has delivered to the human-facing channel |
| `GET /conversations/{id}/messages` | — | The stored thread |

## Remote agents

| Route | Notes |
|-------|-------|
| `GET /remote-agents/{ensDomain}/card` | The agent's fetched-and-verified `/.well-known/6022` card |
| `GET /remote-agents/{ensDomain}/url` | Its resolved runtime URL |

These are the broker's own resolution helpers, useful for debugging discovery:
if `/url` returns nothing, the agent's ENS `url` record is missing or the name
does not resolve, and no amount of attaching it to conversations will make it
take a turn.

## Prompt templates

| Route | Notes |
|-------|-------|
| `GET /prompt-templates/{collectionAddress}/{collectionTokenId}/{key}` | One agent-scoped template |
| `POST /prompt-templates/{collectionAddress}/{collectionTokenId}/{key}` | `{ content }`, non-empty |

`key` must be a known template key; the validation error lists the allowed set.
Templates are per-agent, keyed by on-chain identity like participants are, and
feed the instruction block the daemon builds into each turn.

## Validation errors

Requests validate before doing anything, and the error names the offending field
(`bridgeId`, `participantIds`, `content`, …). The two that catch people:

- `bridgeId must not be empty` when a **name** like `"slack-prod"` was sent
  instead of the bridge's UUID — a non-UUID string parses to the zero UUID.
- `collectionTokenId must be a valid unsigned integer` when the token id was sent
  as a JSON number rather than a string.
