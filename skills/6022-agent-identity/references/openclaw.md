# OpenClaw Integration

> Based on the public description of OpenClaw in
> [External Agents](https://docs.agentic.6022.io/docs/users/external-agents) —
> no live interop testing done yet (unlike [Grok bot](./grok-bot.md)).
> Confirm exact request/response shapes before shipping.

## Shape of the problem

OpenClaw is local-first and personal-device-hosted, connecting to
WhatsApp/Telegram/Slack/Discord, with its own on-chain attestation model.
Two consequences for 6022 integration:

- It may already have an on-chain identity of its own — don't assume a
  fresh mint is needed (see "Skip minting" below).
- "Local-first" likely means intermittent public reachability (personal
  device, not an always-on server) — the biggest risk is the **public
  origin** requirement (Step 2/Prerequisite in [SKILL.md](../SKILL.md)),
  not the wallet or mint.

## Skip minting if already on-chain

If OpenClaw's existing attestation already produces an EVM address it
controls, don't mint a second identity from scratch. Instead:

1. Use `addAgentAddress` on an existing 6022 collection instance/token (if
   one should represent this OpenClaw instance) to register that address
   as an `evm` address on the agent — see
   [contracts.md § post-mint mutation](./contracts.md#post-mint-mutation).
2. Publish/point the agent's ENS `url` text record at wherever OpenClaw
   serves `/.well-known/6022` from.
3. Only fall back to a full mint (Step 1 of [SKILL.md](../SKILL.md)) if
   OpenClaw has no prior on-chain identity to reuse.

## Public origin on a personal device

Since the runtime isn't necessarily an always-on cloud server:

- If OpenClaw runs behind a home connection/NAT, a tunnel (e.g. a reverse
  proxy to a stable public hostname) is required — the ENS `url` record
  must resolve to something always reachable, not the device's transient
  address.
- If reachability is genuinely intermittent, treat this the same as the
  [Grok bot static-origin gate](./grok-bot.md#what-grok-bot-needs-to-stand-up):
  a small always-on relay that queues turns and wakes the OpenClaw runtime,
  rather than advertising an endpoint that times out most of the time.

## Validation before calling it done

Same rule as every other framework — don't register/point ENS at the
origin, and don't tell the owner the integration is live, until the
[loop-test](./a2a-protocol.md#loop-test-verifying-a-live-a2a-integration-end-to-end)
passes end to end (402 → signed retry → real reply from OpenClaw, not a
stub).
