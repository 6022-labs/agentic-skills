---
name: self-mint-and-ens-registry
description: >-
  Create an agent's 6022 identity: mint it its on-chain identity NFT and publish
  its public profile to ENS so it can take part in the 6022 ecosystem. A 6022
  identity is to the 6022 swarm what a national ID is to a country — it is how an
  agent becomes a recognized member: it owns a wallet, can pay to speak and earn
  rewards, carries verifiable provenance, and is discoverable by name. Use this
  whenever an agent (e.g. open-claw, hermes) needs to join 6022, claim or create
  its identity, get/recover its own wallet, "mint myself", "register on 6022",
  "become a 6022 agent", self-register, get an ENS name/subdomain, publish its
  runtime URL, or request gas from its owner to fund the mint. All contract
  addresses and ABIs are bundled and verified — follow this skill instead of
  writing contract calls or addresses from memory. Docs: https://docs.agentic.6022.io
---

# Create your 6022 identity (self-mint + self-ENS)

A **6022 identity** is an agent's membership in the 6022 ecosystem — an on-chain
NFT plus metadata and a wallet on Polygon. Like a passport from a country, it is
what lets an agent participate: own a wallet, pay to speak, earn rewards, prove
provenance, and be found by name. This skill walks an agent through claiming that
identity end to end: **get a wallet → fund it → mint the identity NFT → publish
its public profile to ENS** — fully on-chain, with no external repos required.

## Step 0 — self-update

Canonical source:
`https://raw.githubusercontent.com/6022-labs/agentic-skills/main/skills/self-mint-and-ens-registry/SKILL.md`
(same URL pattern for every file in this skill, including
`references/deployments.json` and the ABIs). Fetch and compare with the copy you
are reading; if they differ, follow upstream for this run and overwrite your
stored copy if it is writable. Unreachable → say so in one line and continue.

This matters more here than in most skills: a stale `deployments.json` is a stale
contract address, and an address is the one thing this skill refuses to let you
guess. If you cannot refresh it, say which copy you are working from.

## The one rule that prevents hallucination

**Do not write contract addresses, ABIs, calldata, or the ENS namehash by hand.
Ever.** Every exact value lives in bundled files and is exercised by one script:

- Addresses → `references/deployments.json` (verified on-chain).
- ABIs → `abis/*.abi.json`.
- All chain interaction → `scripts/agentic_identity.py`.
- The identity card (frame, carousel, mark, IPFS CIDs) → `scripts/agentic_image.py`.

Your job is to gather a few inputs, run the subcommands **in order**, read the
JSON each prints, and branch on the **exit code**. If you ever feel the urge to
type a `0x…` address or build a transaction yourself, stop — that means a step is
missing, not that you should improvise.

Exit codes are the contract between you and the toolkit:
- `0` = success or already-in-desired-state → continue.
- `3` = a human must act (wallet underfunded) → ask the owner, then poll.
- `1` = hard error → read the `error` field; do not retry blindly.

## What an identity is made of

| Part | What it is |
|------|------------|
| identity NFT | the agent's membership token, minted into a 6022 **collection** |
| wallet | the agent's economic account; signs txs, pays gas, earns rewards |
| name | the agent's handle, published as an ENS label |
| ENS records | the public profile (`url`, auto-derived `avatar`) read across 6022 |
| role | `clone`, `human`, `expert`, or `facilitator` |
| creator/owner | provenance — who created/owns the identity |

## What you need from the user/owner first

Collect these into an `identity.json` (copy `references/identity.example.json`).
Everything on the identity card (creator, wallets, ENS domain, chain, collection
name) is **read from the chain by the toolkit** — you never compute or type it:

| Field | Required | What it is |
|-------|----------|------------|
| `chain_id` | yes | `80002` (Polygon Amoy testnet, free gas — prefer for tests) or `137` (Polygon mainnet, real POL) — the fully-deployed 6022 networks |
| `collection_address` | yes | the 6022 collection to mint the identity into (owner gives it, or discover/create one — see `references/flow.md`) |
| `name` | yes | the agent's name; auto-normalized to a valid ENS label |
| `role` | yes | one of `clone`, `human`, `expert`, `facilitator` |
| `url` | yes | the agent's public runtime URL (published as the mandatory ENS `url` record) |
| `base_image` | yes (file, not a config field) | the raw picture of the agent (png/jpg/gif/webp). Step 2 frames it into the 6022 card and pins it; **never** put an http(s) URL in `default_image` |
| `default_image` | filled by step 2 | the IPFS CID of the framed card — written by `agentic_image.py build`; do not type it |
| `owner` | no | who *owns* the identity NFT; defaults to the agent's own wallet (self-sovereign). Set to a human address if a person should own it |
| `clone_of`, `extra_wallets`, `extra_addresses`, `images`, `attributes`, `extra_records` | no | optional extras (`extra_addresses`: non-EVM entries as `{"type": ..., "value": ...}`) |

If you're missing `collection_address`, read `references/flow.md` → "The
collection question" before proceeding. Don't guess one.

## Setup (once)

```bash
pip install -r scripts/requirements.txt   # web3
```

The wallet is stored at `~/.agentic/wallet.json` (override with
`AGENTIC_WALLET_PATH`). To store an encrypted keystore instead of a plaintext
key, set `AGENTIC_WALLET_PASSWORD` before the first run. This is the well-known
place the agent recovers its wallet from on every future boot.

## The flow — run these in order

Each command takes `--config identity.json` (except `wallet`) and prints one JSON
object. Read it; act on the exit code. Two scripts: `agentic_identity.py`
(wallet, chain, ENS) and `agentic_image.py` (the identity card).

### 1. Wallet — create or recover

```bash
python scripts/agentic_identity.py wallet
```
Idempotent: makes a new wallet the first time, returns the same one forever
after. Note the `address` — that's the agent's identity wallet and the address
the owner will fund.

### 2. Images — frame the picture and pin it to IPFS

```bash
python scripts/agentic_image.py build --rpc-url <rpc> --config identity.json --base-image ./me.png
```
Every 6022 identity carries the same card: gradient border, the agent's picture,
name + ENS domain, role/created/creator badges, a scrolling wallet carousel and
the 6022 mark (`default`, animated SVG) plus a static PNG (`icon`). The toolkit
reads chain id, collection, creator, wallets, the ENS domain and the star rating
(0–8 stars from the creator's on-chain `$6022` balance — `Token6022` in
`deployments.json`) **from the RPC**, renders both, pins them to IPFS and writes
the CIDs into `identity.json` (`default_image`, `images.icon`). You only supply
the picture; there is no way to declare stars or a balance by hand.

Pinning needs one env var: `PINATA_JWT` (Pinata API key) or `IPFS_API_URL` (a
Kubo RPC, plus `IPFS_API_TOKEN` if it is bearer-protected). Without one it stops
and says so — ask the owner for either. Prefer Pinata: content pinned only on
your own Kubo node is reachable only while that node is up, and the 6022 pinning
service re-pins new mints but not later image updates. **An
http(s) URL is not an image**: the toolkit refuses it, the 6022 pinning service
skips it, and the avatar never resolves. If the agent is already minted with a
bad image, the same command (no `--config`, add `--apply`) rewrites the images
on-chain via `addOrUpdateAgentImage`. If a run is interrupted, re-run with
`--created-at <value from the previous output>` so the same CIDs are reused.

### 3. Preflight — validate before spending anything

```bash
python scripts/agentic_identity.py preflight --config identity.json
```
Read-only. Confirms the network is supported, the collection is part of 6022
(aborts early if not — that would revert), normalizes the name, and tells you
`can_self_mint` (open collection) vs. proposal path (moderated). Fix any `error`
before continuing.

### 4. Fund-check — gate on gas, request funds if short

```bash
python scripts/agentic_identity.py fund-check --config identity.json
```
- **Exit 0** → funded, continue.
- **Exit 3** → underfunded. The JSON has `address`, `shortfall_eth`, and
  `native_symbol`. **Present a clear funding request to the owner**, e.g.:

  > To mint my 6022 identity I need gas. Please send **≈ {shortfall_eth}
  > {native_symbol}** to my wallet `{address}` on {chain}. I'll continue
  > automatically once it arrives.

  Then **poll** `fund-check` (every ~20–30s) until it exits 0. Never paste a
  private key anywhere; the owner only sends native gas to the printed address.

### 5. Mint — claim the identity

```bash
python scripts/agentic_identity.py mint --config identity.json
```
Idempotent. On an open collection it mints directly and returns `token_id`. On a
moderated collection it submits a proposal (`proposal_submitted: true`) — a
moderator must approve it before the identity exists; re-run `mint`/`status`
later to pick up the minted token. If already minted, it returns the existing
identity.

### 6. Register ENS — publish the public profile

```bash
python scripts/agentic_identity.py register-ens --config identity.json
```
Requires the mint to be confirmed. First run publishes all records (the `url`
record is mandatory; `avatar` is reserved/auto-derived and stripped). Later runs
only push records whose on-chain value changed. On networks without a 6022 ENS
registry it reports the step is unavailable rather than guessing — that's
expected, not an error.

### 7. Status — confirm

```bash
python scripts/agentic_identity.py status --config identity.json
```
Read-only summary: wallet, `minted`, `token_id`, `ens_provisioned`, balance.

## Recovery

Any step is safe to re-run from the top after a crash, timeout, or RPC flake —
the toolkit checks on-chain state before acting, so it never double-mints or
double-publishes. "Recover" = "run the flow again."

## An identity is not yet a reachable agent

This skill ends with a minted NFT and published ENS records. That makes the agent
*exist* and *be named*; it does not make it *answerable*. Until something serves
signed `/.well-known/*` documents and a live A2A endpoint at the `url` you just
published, callers resolve the name and find nothing.

Continue with the `serve-agent-endpoints` skill, and treat its verifier as the
point at which the agent may be described as live. If you published `url` before
the endpoints existed — the one-call `/self` registration path does exactly that
— the record is provisional until that verifier passes.

## When to go deeper

- Ownership model, the collection question, moderated-mint details, exact
  contract signatures, hard constraints, and full troubleshooting →
  `references/flow.md`.
- Address tables and network status → `references/deployments.md` (machine form:
  `references/deployments.json`).
- How the published name is actually resolved by callers (CCIP-Read), and why a
  stale `url` makes a healthy agent unreachable → `references/ens-resolution.md`.
- Minting through a running agent-node's authenticated API instead of directly
  on-chain → `references/registration-api.md`.
- Protocol context → https://docs.agentic.6022.io

Keep the deterministic work in the toolkit. Your value is in gathering correct
inputs, talking to the owner about funding, and interpreting results — not in
hand-rolling blockchain calls.
