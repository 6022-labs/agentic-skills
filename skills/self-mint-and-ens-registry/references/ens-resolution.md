# How a 6022 ENS name resolves (CCIP-Read)

The mint puts an identity on-chain; the ENS records make it **findable by name**.
This file explains what a caller does with the name, which matters when you are
debugging why a correctly-minted agent cannot be reached.

## The name

```
<agent>.<collection>.<chainId>.6022.eth
```

The `chainId` label is not decoration — it tells the resolution gateway which
`AgentEnsRegistry` holds the records. `80002` is Polygon Amoy, `137` is Polygon
mainnet. Records live on whichever chain the agent's collection was deployed to,
which is essentially never Ethereum itself.

## Resolution is ERC-3668, not a normal lookup

Records are on Polygon; ENS resolution starts on Ethereum. CCIP-Read bridges the
two, and the caller never has to know:

1. A client resolves the name on Ethereum (or Sepolia for testnets).
2. `AgentEnsOffchainResolver` reverts with `OffchainLookup`, redirecting the
   client to the off-chain gateway.
3. The client follows the revert: `GET /{sender}/{callData}` on
   `agentic-ens-gateway`.
4. The gateway reads the record straight from `AgentEnsRegistry` on the registry
   chain and **signs** its answer.
5. The L1 resolver verifies that signature and returns the value.

From the caller's side this is an ordinary ENS text-record lookup. The
consequences worth knowing:

- **The client must support CCIP-Read.** One that does not will see a revert, not
  a value. If a name resolves in one tool and not another, suspect this before
  suspecting the records.
- **Records are only as fresh as the registry chain**, and a gateway outage
  breaks resolution while the on-chain data is perfectly intact.

## Records published per agent

| Record | Meaning |
|--------|---------|
| `text("url", …)` | the agent's runtime origin — where `/.well-known/*` and its A2A endpoint are served. **Mandatory** |
| `text("avatar", …)` | image URL. **Reserved** — derived on-chain from the NFT's default image; setting it reverts |
| `addr()` | the agent's `evm` wallet address |

`addr()` is what closes the loop on discovery: it must match the `kid` in the
signatures on the agent's `/.well-known/*` documents. A caller that verifies a
document against the address the name resolves to has proven the document belongs
to the identity behind the name. If those two disagree, the discovery documents
are being served by something that does not control the identity.

## The full discovery path

```
name → ENS url record → GET /.well-known/6022        (verify signature vs addr())
                      → GET /.well-known/agent-card.json → supportedInterfaces[].url
                      → POST that URL with SendMessage   (402 → pay → retry)
```

A stale `url` record after a redeploy is the most common reason a healthy agent
is unreachable: the runtime is fine, and discovery lands somewhere else. Treat
the origin as derived state and re-publish it whenever it changes — the
`serve-agent-endpoints` skill's verifier checks exactly this, with
`--ens-domain`.
