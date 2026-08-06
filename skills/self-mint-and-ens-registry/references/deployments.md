# Deployed 6022 contract addresses

**The machine-readable source of truth is `deployments.json`.** This table is for
humans. Never copy an address from memory — read it from `deployments.json` (the
toolkit does this for you).

## Polygon Amoy testnet — chainId `80002` — status: FULL

Native gas token: **POL**. Explorer: https://amoy.polygonscan.com

| Contract | Address |
|----------|---------|
| AgentCollectionsManager | `0x8095A0D4bE42A61db9e609aA693f516044Bda990` |
| AgentEnsRegistry | `0x37c5879529E5b0241663ed40feeebb2326BBEE0d` |
| AgentCollectionCreatorV1 | `0x94EbaBD4796D0A8d1d416B6696559dE2C5E9F0a1` |
| AgentCollectionV1 (clone template — NOT a mint target) | `0x7dFF34CceE40bc70910C001552b83C8800a69Ef8` |
| AgentCollectionsDescriptor | `0x21D3E43A8b057609C3DC8f12dd3861fD2560754A` |

Preferred chain for tests — free POL from faucets.

## Polygon mainnet — chainId `137` — status: FULL

Native gas token: **POL** (real funds). Explorer: https://polygonscan.com

| Contract | Address |
|----------|---------|
| AgentCollectionsManager | `0xb0dc8a83c700A9BBcc53cA1a2C6993a63129d2F6` |
| AgentEnsRegistry | `0x23dbc335f966d9869940FB55f61E919118AAD236` |
| AgentCollectionCreatorV1 | `0x6919B4A31b27cf5Be422a88F3259f5C7B9470BEa` |
| AgentCollectionV1 (clone template — NOT a mint target) | `0xfCD13C7Cb7Ee8913a1E651308a11D5D05e575dc6` |
| AgentCollectionsDescriptor | `0x7462D55bD41DE313e739d5DA6acfE926952ABbf3` |

## Other chains

No other chain has the collection/ENS stack. They are intentionally absent from
`deployments.json`: treat them as not supported. Do not invent addresses.
