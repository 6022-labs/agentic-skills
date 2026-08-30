# Contracts

## Deployed addresses (from `agentic-smart-contracts/deployments/`)

| Network | ChainId | AgentCollectionsManager | AgentEnsRegistry | AgentCollectionV1 |
|---|---|---|---|---|
| Polygon Amoy (testnet) | `80002` | `0x8095A0D4bE42A61db9e609aA693f516044Bda990` | `0x37c5879529E5b0241663ed40feeebb2326BBEE0d` | `0x7dFF34CceE40bc70910C001552b83C8800a69Ef8` (**implementation, do not mint into it — see below**) |

Other networks (`ethereum/`, `polygon/`, `sepolia/`) follow the same file
layout under `agentic-smart-contracts/deployments/<network>/*.json` — read
the `.chainId` file next to them and the `address` field of each ABI JSON.
**Never invent/guess a contract address** — if a chain isn't in this
directory, it isn't deployed there; stop and say so instead of assuming one.

## Implementation vs. collection instance — do not mint into the implementation

The `AgentCollectionV1` address above (`0x7dFF...` on Amoy) is a **template
contract**, deployed once and never initialized (see
`deploy/AgentCollectionV1.ts`: `// AgentCollectionV1 implementation;
collections are deployed as clones of it.`). `AgentCollectionCreatorV1`
clones it (EIP-1167 minimal proxy via OpenZeppelin `Clones.clone()`) and
calls `initialize(name, symbol, description, admin, moderator,
collectionsDescriptor)` on the **clone**, not on the implementation.

`mint()` must be called on a real **collection instance** (a clone address
an owner created — via `AgentCollectionsManager`/`AgentCollectionCreatorV1`,
or one already given to you), never on the implementation address itself.
If you don't have a collection instance address, ask the owner for one or
create one through `AgentCollectionsManager` — don't assume the
implementation address is usable.

## `mint`

```solidity
function mint(
    address to,                 // future owner (usually the agent's own wallet)
    string memory name,
    AgentAddress[] memory addresses,   // typed addresses, at least one "evm"
    KeyValue[] memory images,          // e.g. {key: "avatar", value: "ipfs://..."}
    KeyValue[] memory attributes,      // free-form key/value metadata
    NullableUint256 memory cloneOf     // set if cloning an existing agent's config
) external;
```

Allowed when the collection has no moderators, or the caller is a
moderator. Otherwise call `createMintProposal(...)` with the same
arguments; a moderator later calls `mintFromProposal(proposalId)` or
`refuseMintProposal(proposalId)`.

`AgentAddress` is `{ addressType: string, value: string }` (e.g.
`{"evm", "0xabc..."}`); `KeyValue` is `{ key: string, value: string }`.

## Post-mint mutation

- `addOrUpdateAgentAttribute(tokenId, key, value)`
- `addAgentAddress(tokenId, addressType, value)` / `removeAgentAddress(...)`
  — only the agent controller (owner or an existing `evm` agent address) may
  call these. Removing the last `evm` address reverts.
- `removeAgentImage(tokenId, key)`

## ENS text records (`IAgentEnsRegistry`)

Published per agent on the same chain as the collection:
- `text("url", <runtime base URL>)` — where the gateway lives.
- `text("avatar", <image URL>)`.
- `addr()` — resolves to the agent's `evm` address.
