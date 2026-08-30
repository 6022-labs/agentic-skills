# ENS & CCIP-Read (Agent Discovery)

Agent name format: `<agent>.<collection>.<chainId>.6022.eth`
(e.g. `hermes.agents6022.eth`). The `chainId` label tells the gateway which
`AgentEnsRegistry` holds the records — `80002` is Polygon Amoy, `137` is
Polygon mainnet. Records live on whichever chain the agent's collection was
deployed to (rarely Ethereum itself); ENS resolution still starts on
Ethereum via ERC-3668 (CCIP-Read).

## Flow

1. A client resolves `hermes.agents6022.eth` on Ethereum (or Sepolia for
   testnets).
2. `AgentEnsOffchainResolver` reverts with `OffchainLookup`, redirecting
   the client to the off-chain gateway.
3. The client follows ERC-3668: `GET /{sender}/{callData}` on
   `agentic-ens-gateway`.
4. The gateway reads the record straight from `AgentEnsRegistry` on the
   registry chain (e.g. Polygon Amoy) and signs the answer.
5. The L1 resolver verifies the gateway's signature and returns the value
   to the client — a normal ENS lookup from the caller's point of view;
   the data never had to live on Ethereum.

## Records published per agent

- `text("url", <runtime base URL>)` — where `/.well-known/6022`,
  `/.well-known/agent-card.json`, `/a2a`, `/responses` are served.
- `text("avatar", <image URL>)`.
- `addr()` — the agent's `evm` wallet address (must match the `kid` in
  signed discovery-document signatures).

An orchestrator resolving a remote agent does, end to end: resolve ENS
`url` record → `GET /.well-known/6022` → invoke `/responses` or `/a2a` →
callback to whatever bridged the conversation.
