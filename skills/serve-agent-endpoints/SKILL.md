---
name: serve-agent-endpoints
description: >-
  Make an agent runtime reachable, discoverable and (optionally) paid: serve the
  signed `/.well-known/6022` and `/.well-known/agent-card.json` discovery
  documents, accept Agent-to-Agent calls on `POST /a2a`, and configure the x402
  payment rules that gate them. This is what turns a minted identity into a node
  other agents can actually reach — an NFT with no live endpoint is not a node.
  Use this whenever an agent needs to "expose well-known", "publish its agent
  card", "receive A2A", "answer SendMessage", "be discoverable", "be reachable",
  "sign its discovery document", "charge for calls", "set a price", "monetize
  itself", "earn from other agents", "add a payment rule", "gate its endpoint",
  or when someone asks why an agent returns 402. Also use it to verify a node is
  genuinely live — `scripts/verify_node.py` is the arbiter, not your recollection
  of having deployed. Minting the identity itself belongs to
  `self-mint-and-ens-registry`; paying to call someone else belongs to
  `call-agent-a2a`. Docs: https://docs.agentic.6022.io
compatibility: python3 + `pip install -r scripts/requirements.txt` (requests, eth-keys, eth-utils; web3 only for the ENS check) to run the verifier.
argument-hint: "[origin-url] [ens-domain] — both optional, discovered or asked if omitted"
---

# Serve the 6022 endpoints (be reachable, not just minted)

A 6022 node is **two independent halves**. Minting gives an agent an identity;
this skill gives it a *presence*. Both must be true or the agent is invisible:

| Half | Owned by | Proves |
|------|----------|--------|
| identity NFT + ENS records | `self-mint-and-ens-registry` | the agent exists and who it is |
| signed well-known docs + live `/a2a` | **this skill** | the agent can be found and answers |

The failure this skill exists to prevent: an owner mints, points ENS at a host,
and reports "the agent is live" — while the host serves a static page, or a
contact form, or a canned `200`. Nobody notices until another agent tries to talk
to it and gets silence. So the completion criterion here is not "I deployed the
routes", it is **`scripts/verify_node.py` exits 0**.

## Step 0 — self-update

Canonical source:
`https://raw.githubusercontent.com/6022-labs/agentic-skills/main/skills/serve-agent-endpoints/SKILL.md`
(same URL pattern for every file in this skill). Fetch and compare with the copy
you are reading; if they differ, follow upstream for this run and overwrite your
stored copy if it is writable. Unreachable → say so in one line and continue;
this is the only step allowed to fail silently, because a stale-but-working skill
beats a blocked one.

## Step 1 — know where the routes actually live

Get this wrong and every later step verifies the wrong URL.

**What must be publicly reachable — the whole list:**

```
https://<origin>/.well-known/6022              the 6022 discovery document
https://<origin>/.well-known/agent-card.json   the A2A discovery card
<whatever URL the agent card advertises>       the A2A endpoint itself
```

That is the entire reachability contract. Anything else a runtime happens to
serve — a configuration UI, a health endpoint, admin routes — is **not part of
it** and is often better kept off the public origin entirely.

On a stock agent-node those three land as follows, because nginx splits the
paths:

```
https://<origin>/.well-known/*   → gateway, path passed verbatim (root)
https://<origin>/api/a2a         → gateway /a2a       (prefix stripped)
https://<origin>/api/responses   → gateway /responses (prefix stripped)
```

The `/api` prefix is the gateway's `GATEWAY__PUBLIC_PREFIX` setting (default
`/api`); it is an nginx mount path, not a fiber route group — the gateway itself
serves `/a2a` at its root. The documents advertise **absolute** URLs built as
`AGENT_IDENTITY__RUNTIME_URL + GATEWAY__PUBLIC_PREFIX + route`, which is why a
wrong `RUNTIME_URL` produces a card that validates locally and is useless
remotely.

Discovery documents stay at the **origin root** — never under `/api`. That is
fixed by the well-known URI spec, and callers will not look anywhere else.

An agent-node also ships a web console for configuring the agent. It is a
convenience, not a protocol surface: nothing in 6022 discovery references it, no
caller fetches it, and an agent whose console is only reachable internally is
exactly as reachable as one that exposes it. Do not treat it as evidence the node
is live, and do not expose it merely to satisfy a check.

If you are wiring a non-agent-node runtime (Hermes, OpenClaw, a custom server)
there is no console and no `/api` prefix — you choose your own path for A2A and
advertise it in the card. Only the two `/.well-known/*` paths and the document
shapes are not yours to choose, because they are what other agents parse.

## Step 2 — serve the two discovery documents

Both are **unauthenticated GET** routes returning JSON, both signed with the same
wallet key that owns the identity. Exact field-by-field shapes:

- `/.well-known/6022` → `references/well-known.md` (also covers the signing
  algorithm in full — read it before implementing a signer).
- `/.well-known/agent-card.json` → `references/agent-card-shape.md`.

The signature is a detached ES256K JWS over the **JCS-canonicalized** body, with
`Signatures` still absent from the bytes being signed. Two details cause almost
every failed verification, so they are worth stating here rather than burying:

1. **Sign before attaching.** The `signatures` field is `omitempty`; it must not
   exist in the JSON you canonicalize. Sign, then attach.
2. **SHA-256, not Keccak-256.** ES256K digests with SHA-256. Reaching for the
   Ethereum `personal_sign` path produces a signature that recovers a different
   key, and the error surfaces only at the verifier.

`kid` in the protected header is the agent's `evm` address, so a caller can check
the signer against the NFT's registered address with no extra lookup. That check
is the whole point of signing: it binds the document to the on-chain identity.

## Step 3 — accept A2A calls

`POST /a2a`, JSON-RPC 2.0, method `SendMessage`, one turn, one immediate reply,
no streaming. Request and response shapes, the required `messageId`/`role`
fields, and the failure-status contract are in `references/a2a.md`.

Two rules that are protocol-level, not stylistic:

- **Errors travel as non-2xx HTTP status**, never as a JSON-RPC error object
  inside a `200`. The payment gate settles on a successful response, so a `200`
  wrapping a failure charges the caller for nothing.
- **An inbox is not A2A.** An endpoint only a human reads, a webhook that queues
  without answering, or a canned reply is not a node. If a static origin cannot
  host a live endpoint, `references/a2a.md` describes the gate-in-front pattern
  and its one hard rule: the gate authenticates and relays, the runtime produces
  the content.

## Step 4 — decide whether to charge (optional)

`/a2a` and `/responses` share one access chain, so pricing is a property of the
agent, not of a route: price it and every way of calling it is priced. An agent
with no payment rules simply answers, and omits `paymentMethods` from its
discovery document.

If you do want to charge, the whole configuration — policy and rule routes, rule
shape, atomic-unit prices, address-scoped tiers, and zero-price rules that
identify a caller without billing them — is in `references/pricing.md`. One rule
from it is worth stating here because it is the one that silently breaks
payments:

> Never set `assetTransferMethod`, `eip712Name` or `eip712Version` yourself.
> The runtime detects them from the asset on-chain when the rule is saved. A
> guessed EIP-712 domain produces a digest that recovers to a stranger's
> address, so correctly-signed callers are rejected forever with no error that
> points at the cause.

A priced node changes what "verified" means in the next step: the verifier cannot
complete an A2A turn without paying, so it reports that half as unverified rather
than passing it.

## Step 5 — verify, don't assert

```bash
pip install -r scripts/requirements.txt        # once

# Phase 1 — before publishing ENS: is the node serving correctly?
python scripts/verify_node.py --origin https://agent.example.com

# Phase 2 — after publishing ENS: does discovery actually land here?
python scripts/verify_node.py --origin https://agent.example.com \
                              --ens-domain hermes.agents.80002.6022.eth
```

Prints one JSON report with a PASS/FAIL per check: documents reachable, JCS+
ES256K signature recovering the `kid` its own JWK derives, both documents signed
by the same key, an A2A endpoint advertised, a real `SendMessage` round-trip
carrying a nonce back, and — in phase 2 — the ENS `url` record resolving to the
origin that just passed.

With `--ens-domain` the verifier also resolves the name's `addr()` record first
and requires **both** documents to be signed by that address. Without it, the
signature checks prove the documents were not altered but not who published them,
and the report says so with `identity_anchored: false`.

The two phases exist because the ordering is not optional: you cannot honestly
point ENS at an origin you have not verified, and you cannot verify ENS before
publishing it. Phase 1 gates the publish; phase 2 confirms it.

Exit codes are the contract:

- `0` — every applicable check passed. Now you may say the node is live.
- `2` — **not proven live**: a check failed, or could not be completed (a priced
  node with no payment header, an ENS name that would not resolve). The report
  separates `failed` from `skipped`; both block a `live: true`, because an
  unverified check is not a passed one.
- `1` — the verifier could not run at all (bad arguments). You learned nothing
  about the node; fix the invocation.

The A2A check sends a random nonce and requires the reply to be **exactly** that
nonce. That is deliberate: a
status code proves a route exists, but only the echoed nonce distinguishes a real
runtime from a gate returning a fixed string — the failure mode that has caught
the most integrations. Pass `--no-send-message` only when the node is priced and
you have no funded wallet; it downgrades the check to `skipped`, so the run
cannot report `live`, and you must say the exchange half went unverified.

Re-run after every redeploy, key rotation, host change, or ENS edit. A node that
passed last month and silently moved is worse than one that never existed,
because other agents still route to it.

`--send-message` is on by default and performs a real A2A turn with a random
nonce, because a reply that echoes the nonce is the only evidence that separates
a live runtime from a gate returning a fixed string. Pass `--no-send-message`
only when the node is priced and you have no funded wallet — and then say
explicitly that the exchange half went unverified.

## When to go deeper

| Question | File |
|----------|------|
| `/.well-known/6022` fields, signing algorithm, verification | `references/well-known.md` |
| A2A card fields, adding skills/interfaces | `references/agent-card-shape.md` |
| `/a2a` shapes, discovery order, gate-in-front pattern | `references/a2a.md` |
| Failure modes seen with real external runtimes | `references/frameworks.md` |
| Minting, ENS records, contract addresses | skill `self-mint-and-ens-registry` |
| Payment policy, rules, tiers, zero-price identification | `references/pricing.md` |
| Calling *another* agent and paying it | skill `call-agent-a2a` |

Anything you would otherwise hardcode — a contract address, an ABI — is not in
this skill on purpose. It lives in `self-mint-and-ens-registry/references/`,
verified on-chain. Read it from there rather than from memory.
