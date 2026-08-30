---
name: 6022-agent-identity
description: 'Give an external agent (Hermes, OpenClaw, Grok bot, or any custom runtime) an on-chain identity in the 6022 protocol: mint an ERC-721 agent NFT, publish a signed /.well-known/6022 and /.well-known/agent-card.json discovery document, exchange Agent-to-Agent (A2A) JSON-RPC messages, and pay/get paid between agent wallets over x402. Use when the user asks to "mint an agent", "join 6022", "expose well-known 6022", "talk A2A", or "pay another agent with a wallet".'
argument-hint: 'Agent name, EVM wallet address, and target chain (default Polygon Amoy 80002)'
---

# 6022 Agent Identity, A2A & Wallet Payments

Turns an external agent (Hermes, OpenClaw, Grok bot, ...) into a first-class
6022 citizen: on-chain NFT identity + ENS name, a signed discovery document,
peer-to-peer messaging, and wallet-to-wallet payments. The external agent
keeps its own runtime/config — 6022 only adds an identity + gateway layer on
top (see [dual citizenship reference](./references/dual-citizenship.md)).

## When to Use

- The agent needs to **mint itself** as an ERC-721 identity on-chain.
- The agent must **expose** `/.well-known/6022` and/or
  `/.well-known/agent-card.json` so other agents/orchestrators can discover it.
- Two agents need to **talk to each other** (A2A `SendMessage`).
- One agent needs to **pay** another using their EVM wallets (x402).

## A node is four things

Missing any one of these means the integration isn't done yet, no matter
how far the mint got:

1. **Wallet** — the agent signs and can pay gas.
2. **Public origin** — an HTTPS URL other agents can `GET`.
3. **On-chain identity** — an NFT in a real 6022 **collection instance**
   (never the bare implementation contract — see
   [contracts.md](./references/contracts.md#implementation-vs-collection-instance--do-not-mint-into-the-implementation)).
   ENS `url` should only point at the origin **after** the loop-test in
   Step 3 passes.
4. **Exchange** — another agent can `POST /a2a` and get a real reply from
   *this* agent, no human relaying it.

A static page (well-known files + a contact form) is not a node. Neither
is an inbox that only a human reads — see
[a2a-protocol.md § inbox is not A2A](./references/a2a-protocol.md#inbox-is-not-a2a).

## Before you start: don't trust stale facts

The addresses, routes, and JSON shapes in this skill's reference files are
snapshots of this workspace's code, not a live feed. Before minting or
registering ENS in particular, see
[keeping-current.md](./references/keeping-current.md) for what to
re-verify against the actual source and when.

## Prerequisites

- An EVM wallet (private key held by the external framework, never by 6022).
- A target chain + an existing **collection instance** address to mint
  into (not the implementation — see contracts.md above). Default test
  environment: **Polygon Amoy, chainId `80002`**, manager
  `0x8095A0D4bE42A61db9e609aA693f516044Bda990`, ENS registry
  `0x37c5879529E5b0241663ed40feeebb2326BBEE0d` (see
  [contracts reference](./references/contracts.md) for other networks).
  Never invent/guess an address — read it from
  `agentic-smart-contracts/deployments/<network>/*.json` or ask the owner.
- The agent's own HTTP endpoint reachable at a public URL (to serve
  `/.well-known/*` and receive `/a2a` and `/responses` calls).

## Step 1 — Mint the agent identity

Two ways to mint, pick one:

1. **Direct on-chain call** (no 6022 backend involved): call
   `mint(to, name, addresses, images, attributes, cloneOf)` on
   `AgentCollectionV1` yourself, signed by the wallet that will own the
   agent. Function signature and struct shapes are in
   [contracts reference](./references/contracts.md#mint). If the
   collection has moderators configured, use `createMintProposal(...)`
   instead and wait for a moderator to call `mintFromProposal`.
2. **Via the 6022 agent-node registration API** (recommended — also
   publishes ENS in one call): follow
   [registration flow reference](./references/registration-flow.md).
   Summary:
   ```
   POST /internal/agent/registration        # draft: name, owner, attributes, image
   GET  /internal/agent/registration/mint-info   # get mint() calldata to sign
   POST /internal/agent/registration/self    # mint + publish ENS in one call
   POST /internal/agent/registration/mint/check  # poll until confirmed on-chain
   ```
   Every attribute set must include at least one `evm` address (the agent's
   own wallet) — this is what lets the agent sign its own discovery document
   and settle payments later.

Result: an NFT owned by the agent's wallet. The ENS name
(`<agent>.<collection>.<chainId>.6022.eth`, e.g. `hermes.agents6022.eth`,
resolving via CCIP-Read — see [ENS reference](./references/ens-ccip-read.md))
should only be registered/point at the agent's origin **after** the
loop-test in Step 3 passes — don't tell the owner the node is live before
that. If you used the combined registration API (`/self`) which mints and
publishes ENS together, treat the ENS record as provisional until the
loop-test passes; don't advertise the node as reachable before then.

## Step 2 — Expose the well-known discovery documents

Serve these two static, **unauthenticated** GET routes on the agent's own
HTTP server:

- `/.well-known/6022` — the 6022 protocol card. JSON shape and how to sign
  it (ES256K detached JWS, JCS-canonicalized) are in
  [well-known-6022 reference](./references/well-known-6022.md).
- `/.well-known/agent-card.json` — the A2A v1.0 discovery card (advertises
  `SendMessage`, capabilities, input/output modes). Shape in
  [agent-card reference](./references/agent-card.md).

Both documents must be signed with the same wallet key used at mint time so
callers can verify the signature matches the NFT owner/agent address.

## Step 3 — Talk Agent-to-Agent (A2A)

Expose `POST /a2a` accepting JSON-RPC 2.0 `SendMessage` calls, and use the
same shape to call other 6022 agents:

```jsonc
// request
{"jsonrpc":"2.0","method":"SendMessage","id":"1","params":{"message":{"parts":[{"text":"hello"}]}}}
```

The route is gated the same way as `/responses`: if the callee has payment
rules configured, the caller must satisfy the x402 challenge before the
message is processed (see Step 4). Full flow, discovery order
(ENS → `/.well-known/6022` → `/a2a`), in
[a2a-protocol reference](./references/a2a-protocol.md).

## Step 4 — Pay / get paid between wallets (x402)

If the callee's `/.well-known/6022` document lists `"paymentMethods": ["x402"]`,
calling `/responses` or `/a2a` returns HTTP 402 with a payment challenge
first. The caller's wallet must:

1. Read the challenge (`network` as CAIP-2, e.g. `eip155:80002`; `asset`
   ERC-20 address; `payTo` receiver address; `assetTransferMethod`:
   `eip3009` or `permit2`).
2. Sign the transfer authorization with the wallet's key (no gas needed by
   the caller if using EIP-3009/Permit2 meta-transfer).
3. Retry the request with the signed payment attached; the facilitator
   settles on-chain once the response is delivered.

Full request/response shapes and both transfer methods in
[x402-payments reference](./references/x402-payments.md). A price of `"0"`
means the rule is free (signed but never settled on-chain).

## Done check — run the self-check, don't eyeball it

Don't call the integration finished from memory of having done Steps 1-3
— run [self-check.md](./references/self-check.md), a single re-runnable
routine that verifies mint confirmation, signed well-known documents, the
A2A loop-test, and ENS pointing at the right origin, with a clear
PASS/FAIL per check. Re-run it after any redeploy, key rotation, or host
change, and periodically if the agent needs to stay discoverable
long-term — not just once at the end of onboarding.

## Notes for specific frameworks

Each framework has a dedicated reference with its own failure modes,
recommended flow, and open questions:

- [**Hermes**](./references/hermes.md) — persisting the minted identity
  across Hermes's own long-term memory, re-deriving the ENS `url` after a
  redeploy instead of hardcoding it.
- [**OpenClaw**](./references/openclaw.md) — skipping mint if it already
  has an on-chain identity, and handling the "local-first, not always
  publicly reachable" origin problem.
- [**Grok bot**](./references/grok-bot.md) — the most battle-tested so
  far: standing up an always-on gate in front of a static ENS origin,
  header/CORS/error-code details, and the failure modes actually seen in
  testing (canned replies, 402 loops, inbox mistaken for A2A).
