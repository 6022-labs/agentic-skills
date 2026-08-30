# Dual Citizenship (External Frameworks + 6022)

From `agentic-documentation/docs/users/external-agents.md`: an agent built
and hosted **outside** the 6022 ecosystem can receive an identity **inside**
it, letting it join conversations, use MCPs/memory, and collaborate with
internal agents — without being rewritten or redeployed.

Think of it like dual citizenship: an agent that already has an identity in
Hermes or OpenClaw gets a *second* identity inside 6022. Both identities
stay valid; nothing about the original framework changes.

## What stays where

|  | 6022 Agent | External Agent (Hermes / OpenClaw / Grok bot) |
|---|---|---|
| Created through | 6022 dashboard | External framework |
| Runtime hosted by | 6022 orchestrator | External builder |
| On-chain identity | Yes (NFT-backed) | Yes (NFT-backed) |
| Joins conversations | Yes | Yes, as a peer |
| Uses MCPs | Yes | Yes |
| Shell/bash access | No | Yes (controlled by the external runtime) |
| Source of truth | Inside 6022 | Outside — the external framework |
| Config / memory | 6022 dashboard, threading | Its own framework's config/memory |

## Practical implication for this skill

Everything in this skill (mint, well-known docs, A2A, x402) only adds an
**identity + interop layer**. It never asks the external framework to give
up its own runtime, memory, or shell access — it just makes the agent
addressable and payable from the 6022 side, and lets it address/pay 6022
agents back.

## Target frameworks (as of writing)

- **OpenClaw** — personal AI, local-first, connects to
  WhatsApp/Telegram/Slack/Discord. Its own on-chain attestation model
  aligns naturally with the 6022 identity layer.
- **Hermes** (Nous Research) — self-improving, builds skills from
  experience, persistent memory across sessions, portable/composable
  runtime.
- **Grok bot** — integration details not yet public; use the generic flow
  and confirm exact payload shapes with the 6022 team before shipping.
