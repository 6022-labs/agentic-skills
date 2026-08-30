# Keeping This Skill Current (No Remote Update Server)

There is no hosted registry serving this skill — it's a local file in this
workspace. "Staying current" means: never trust the hardcoded constants
in this skill (contract addresses, endpoints, ABIs) as permanently true —
**re-derive them from the source of truth before any action that spends
gas, mints, or registers ENS.**

## What can go stale

| Hardcoded here | Source of truth | Re-check before |
|---|---|---|
| Manager / ENS registry / implementation addresses ([contracts.md](./contracts.md)) | `agentic-smart-contracts/deployments/<network>/*.json` | Any mint / ENS write |
| Registration API routes ([registration-flow.md](./registration-flow.md)) | `agentic-agent-node/src/agent_identity_mvc/agent_registration_controller.go` | Calling the registration API |
| `/.well-known/*` routes and shapes ([well-known-6022.md](./well-known-6022.md), [agent-card.md](./agent-card.md)) | `agentic-agent-node/src/gateway_mvc/core/well_known_controller.go` and `gateway/core/use_cases/` | Publishing/parsing a card |
| x402 config fields ([x402-payments.md](./x402-payments.md)) | `agentic-agent-node/src/agent_payment/models/*` | Building/validating a payment rule |

## Procedure

1. Before minting or registering ENS on a **new** network, re-read
   `agentic-smart-contracts/deployments/<network>/*.json` directly — don't
   trust the table in [contracts.md](./contracts.md) if the network isn't
   Polygon Amoy (`80002`), and re-confirm even Amoy's addresses if this
   skill is more than a few weeks old relative to the last deployment.
2. If a referenced file/route doesn't exist anymore, or its shape
   changed, **update this skill's `.md` files to match** — don't silently
   work around a stale instruction, fix the source of truth in the skill
   itself so the next run doesn't repeat the same mistake.
3. After editing any `.md` file under `.claude/skills/6022-*`, re-zip if a
   packaged copy is distributed elsewhere (Desktop, another agent's
   skills folder) — a stale zip next to an updated source folder is worse
   than no zip, because it looks current.
4. If you're an external agent (Grok bot, Hermes, OpenClaw, ...) that
   fetched a copy of this skill rather than reading this workspace
   directly, and you notice a fact here contradicts what a live 6022
   node actually returns (e.g. `/.well-known/6022` shape differs from
   [well-known-6022.md](./well-known-6022.md)), trust the live node's
   response and flag the discrepancy back to whoever maintains this
   skill instead of silently adapting every time.

## What this is *not*

This is not an auto-update client, a version pin, or a package manager.
There's no `skill.json` with a semver to bump and no remote endpoint to
poll. If a genuine remote-update mechanism gets built later (e.g. a
canonical `/.well-known/agent-skills/` a live node serves), document it
here — until then, "current" means "matches the actual code in this
workspace right now", checked by reading that code, not by trusting a
cached copy.
