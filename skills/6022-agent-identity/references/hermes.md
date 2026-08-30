# Hermes Integration

> Based on the public description of Hermes Agent (Nous Research) in
> [External Agents](https://docs.agentic.6022.io/docs/users/external-agents) —
> no live interop testing done yet (unlike [Grok bot](./grok-bot.md)).
> Confirm exact request/response shapes before shipping.

## Shape of the problem

Hermes is self-improving with persistent memory across sessions and a
composable/portable runtime — the opposite failure mode from a static
origin (Grok bot): Hermes can likely run the full live `/a2a` + `/responses`
surface directly, but its memory layer needs to durably track the 6022
identity across restarts/redeployments.

## What to persist across sessions

- The minted **token id** and the **collection instance** address it
  belongs to (never the implementation address — see
  [contracts.md](./contracts.md#implementation-vs-collection-instance--do-not-mint-into-the-implementation)).
- The **wallet address** (and secure access to its key) used both to mint
  and to sign `/.well-known/6022` / `/.well-known/agent-card.json`.
- The **current origin URL** — if Hermes redeploys to a new host, re-derive
  and re-publish the ENS `url` text record rather than hardcoding the old
  one anywhere in memory or prompts.

## Recommended flow

1. On first run: mint (Step 1 of [SKILL.md](../SKILL.md)), persist the
   token id + wallet in Hermes's own long-term memory store.
2. On every subsequent run: read the persisted identity, verify the
   current origin still resolves `/.well-known/6022` correctly signed by
   the same wallet — re-sign/re-publish if the origin changed.
3. Before advertising `/a2a` as usable, run the
   [loop-test](./a2a-protocol.md#loop-test-verifying-a-live-a2a-integration-end-to-end)
   — this applies to Hermes too, not just gated/static integrations.

## Open questions (confirm before production use)

- Whether Hermes's "skills from experience" mechanism should treat 6022
  A2A calls as a first-class skill (recommended) or a bolted-on tool.
- How Hermes's multi-deployment story (portable runtime across hosts)
  interacts with the ENS `url` record staying accurate — a stale `url`
  after a redeploy will make the agent unreachable via 6022 discovery
  even though Hermes itself is fine.
