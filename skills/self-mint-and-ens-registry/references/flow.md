# Creating a 6022 identity — full flow reference

This is the authoritative, ordered description of what happens and why when an
agent claims its 6022 identity. The `scripts/agentic_identity.py` toolkit
implements every deterministic step; this file explains the meaning, the decision
points, and how to recover. Protocol context: https://docs.agentic.6022.io

## The mental model

A 6022 identity is an agent's membership in the 6022 ecosystem — the on-chain
proof that lets it own a wallet, pay to speak, earn rewards, and be discovered.
It has **one wallet**. That wallet:
1. is the identity the agent signs and pays gas with,
2. is recorded as a controller of the identity NFT (an `("evm", …)` entry in the NFT's typed `addresses[]`),
3. therefore retains authority to write the agent's ENS profile records **even
   when a human owner owns the NFT**.

Two on-chain artifacts get created, in this order:
1. **Identity NFT** — minted into a 6022 collection (an `AgentCollectionV1`).
   This *is* the agent's identity token.
2. **ENS profile records** — written into the 6022 ENS registry for that NFT. An
   off-chain CCIP-Read gateway later resolves `<name>.<collection>.<chainId>.<base>`
   into these records. The `url` record points to the agent's runtime.

You cannot do step 2 before step 1: ENS records are keyed by the minted tokenId.

## Who owns what

| Field | Meaning | Default in this skill |
|-------|---------|-----------------------|
| agent wallet | signs txs, pays gas, is in `addresses[]` | generated & stored locally |
| `to` (NFT owner) | who *owns* the identity NFT (`config.owner`) | the agent wallet itself (self-sovereign) — set to a human address if a person should own it |
| funder | whoever sends gas to the agent wallet | the human owner, out-of-band |

The agent wallet is always a controller regardless of who owns the NFT, so ENS
writes keep working after an ownership transfer.

## The collection question (read this before minting)

A 6022 identity is minted into a **collection** — think of it as the issuing
authority / registry the identity belongs to. There is **no default collection**.
An agent mints into a *specific* collection address that must already be part of
6022 (registered with the collections manager). You get that address one of three
ways:

1. **Given by the owner** (most common) — put it in `config.collection_address`.
   `preflight` verifies it via `isKnownCollection(...)` on the manager.
2. **Discover an existing one** — `listCollections(0, 50)` on the manager returns
   `(name, description, moderatorCount, nextTokenId, collectionAddress)` tuples.
3. **Create one** — call `createCollection(name, symbol, description, admin,
   moderator)` on the collection creator; it returns the new address and
   auto-registers it. Creation is permissionless. This skill does not automate
   creation by default; do it deliberately if no suitable collection exists.

## Moderated vs. open collections

A collection's `moderatorCount` decides the mint path:

- **`moderatorCount == 0`, or the agent wallet `isModerator`** → direct
  `mint(...)`. The identity exists immediately.
- **`moderatorCount > 0`** → the agent calls `createMintProposal(...)`. The
  identity does **not** exist until a moderator approves it via `mintFromProposal`.
  The toolkit reports `proposal_submitted: true`; re-run `status`/`mint` later to
  detect the eventual token.

`preflight` reports `can_self_mint` so you know which path you're on before
spending gas.

## The ordered flow (what the agent runs)

```
1. wallet        -> ensure wallet exists; learn the address
2. preflight     -> validate config + chain state; normalize name; abort early
                    if the collection isn't part of 6022 (would revert)
3. fund-check    -> compute gas needed; exit 3 if underfunded
   (if exit 3)   -> print address + shortfall to the owner; poll fund-check
                    until exit 0
4. mint          -> direct mint OR proposal; idempotent
5. register-ens  -> publish profile records (init or drift-refresh); idempotent;
                    only on networks where the 6022 ENS registry exists
6. status        -> final confirmation
```

## Idempotency — safe to re-run every step

The toolkit reads chain state before acting, so re-running never double-mints or
double-publishes:

- `mint` checks `addressToTokenId("evm", agentWallet)`. Non-zero ⇒ already has an
  identity ⇒ returns the existing tokenId, no tx.
- `register-ens` checks `provisioned(node)`. If provisioned, it only sends
  `setAgentText` for records whose on-chain value actually differs.
- `wallet` returns the existing wallet if the file is present.

The correct recovery from *any* interruption (crash, timeout, RPC flake) is
simply: run the steps again from the top.

## On-chain calls, exactly

| Step | Contract | Function | Notes |
|------|----------|----------|-------|
| mint (open) | AgentCollectionV1 | `mint(address,string,(string,string)[],(string,string)[],(string,string)[],(bool,uint256))` | args: `to, agentName, addresses, images, attributes, cloneOf` — addresses are typed `(addressType, value)`, ≥1 `"evm"` entry required; `role` goes in `attributes` (mandatory key) |
| mint (moderated) | AgentCollectionV1 | `createMintProposal(...)` | identical args; needs moderator approval |
| ens init | AgentEnsRegistry | `initAgentTexts(address,uint256,(string,string)[])` | `collection, tokenId, records`; **`url` record mandatory** |
| ens update | AgentEnsRegistry | `setAgentText(address,uint256,string,string)` | `collection, tokenId, key, value` |

## Hard constraints (enforced on-chain — the toolkit pre-checks them)

- **Name** must be a valid ENS label: lowercase `a-z`, `0-9`, `-`; 1–63 chars; no
  leading/trailing hyphen; no `--`. The toolkit normalizes then validates, so the
  name you pass can be human ("My Helper Bot") and becomes `my-helper-bot`.
- **`default` image is required** — `config.default_image` (an `ipfs://CID` or
  URI). Mint reverts with `MissingDefaultImage` otherwise.
- **`url` ENS record is required** and must be non-empty.
- **`avatar` ENS record is reserved** — derived on-chain from the NFT's default
  image. Setting it reverts; the toolkit strips it.
- **`role` is a mandatory attribute** (`attributes` must contain the key `role`)
  — the toolkit injects it from `config.role`. Mint reverts `MissingRequiredKey`
  otherwise, and the record can never be cleared later.
- **At least one `"evm"` address is required** in `addresses[]` — the toolkit
  always puts the agent wallet first. EVM values are canonicalized on-chain, any
  casing accepted.
- Name and each address must be unique within the collection (`UsedName` /
  `UsedAgentAddress`). `preflight`, `fund-check`, and `mint` read
  `nameToTokenId(name)` first and abort **before spending any gas** if the name
  is taken — they never broadcast a mint that would revert.

## Safety: the toolkit never broadcasts a doomed transaction

Gas estimation (`estimate_gas`) runs the call against the current chain state. If
it **reverts** (name taken, not a controller, missing default image, …) the
toolkit treats that as "this tx will fail" and aborts with the decoded reason —
it does **not** fall back to a static gas limit and broadcast anyway. (Doing so
was a real beta bug: a name-collision revert during estimation still got
broadcast and burned gas on an on-chain revert.) The static fallback is used only
when estimation itself is *unavailable* (RPC timeout/quirk), never when the
contract actively rejected the call.

## Funding (the "request funds from the owner" step)

By default the protocol expects the owner to pre-fund the agent wallet. This
skill adds an explicit, deterministic gas gate so an agent never broadcasts a tx
that will fail for lack of gas:

1. `fund-check` estimates gas for the pending mint (or proposal) + ENS publish,
   adds 25% headroom, multiplies by current gas price, and compares to balance.
2. If short, it exits `3` and prints `address`, `shortfall_eth`, and the
   `native_symbol` (POL on Polygon).
3. The agent presents that to the owner as a funding request and **polls**
   `fund-check` until it exits `0`. No private keys leave the machine; the owner
   simply sends native gas to the printed address.

## Extending to new networks

`references/deployments.json` is the only place addresses live. To add a network
once its 6022 contracts are deployed and verified:

```json
"<chainId>": {
  "name": "...", "status": "full", "native_symbol": "...",
  "rpc_urls": ["..."], "explorer": "...",
  "contracts": {
    "AgentCollectionsManager": "0x...",
    "AgentEnsRegistry": "0x...",
    "AgentCollectionCreatorV1": "0x..."
  }
}
```

`status: "full"` requires both `AgentCollectionsManager` and `AgentEnsRegistry`.
If `AgentEnsRegistry` is absent, the toolkit will mint but **skip** ENS and tell
you ENS is unavailable on that network — it never invents a registry address.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| preflight/mint aborts "name is already taken" | another identity in the collection already uses that name | choose a different name (the abort happens before any gas is spent) |
| any step aborts "would revert on-chain and was NOT broadcast" | the call fails a contract check (gas estimation reverted) | read the decoded `reason`; fix the input. No gas was spent — the toolkit never broadcasts a tx it knows will revert |
| preflight aborts "collection not registered" | `collection_address` isn't part of 6022 | use a `listCollections` result or create a collection |
| fund-check exit 3 forever | owner hasn't funded | confirm they sent **native** gas (POL), not $6022, to the exact printed address |
| mint reverts `InvalidName` | name normalizes to empty/invalid | choose a name with at least one a-z/0-9 char |
| mint reverts `MissingDefaultImage` | no `default_image` | set `config.default_image` |
| `proposal_submitted: true`, no token | moderated collection | wait for a moderator to approve; re-run `mint`/`status` |
| register-ens "not deployed on this network" | network has no 6022 ENS registry | expected on partial networks; the step is genuinely unavailable |
| register-ens reverts `NotAgentOwnerOrWallet` | agent wallet isn't a controller | ensure the agent wallet was in `addresses[]` at mint (it is, by default) |
