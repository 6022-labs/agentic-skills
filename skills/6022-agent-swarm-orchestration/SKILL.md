---
name: 6022-agent-swarm-orchestration
description: 'Trigger and manage a conversation involving multiple 6022 agents (a swarm): register a conversation with participants, attach/detach agents mid-conversation, and let the daemon cycle through participants to invoke each one via A2A. Use when the user asks to "start a conversation between agents", "add an agent to the conversation", "trigger a swarm", or "orchestrate multiple agents on one thread".'
argument-hint: 'Bridge (e.g. Slack), initial participants, and a description'
---

# Orchestrate a Multi-Agent Conversation (Swarm)

Grounded in `agentic-orchestrator/agent-swarm`'s `conversation_broker`.
Companion to [6022-a2a-initiate](../6022-a2a-initiate/SKILL.md) (single
caller → single callee) — this skill is for **N agents on one thread**,
with a daemon deciding who speaks next.

## When to Use

- A conversation needs more than one agent participating (a facilitator +
  specialists, or a human + several agents).
- You need to add or remove an agent from an already-running conversation.
- You're wiring a new external channel (Slack, etc.) to trigger
  conversations rather than calling agents directly.

Every participant agent invoked here should already pass
[6022-agent-identity's self-check](../6022-agent-identity/references/self-check.md)
(mint confirmed, well-known signed, A2A loop-test green) — attaching a
participant that isn't actually operational just produces silent skipped
turns.

## Core model

| Concept | What it is |
|---|---|
| **Conversation** | One thread, tied to one `bridgeId` (e.g. `slack-prod`). Has a description, can be concluded. |
| **Participant** | A human, agent, or facilitator. Agents carry an `ensDomain`. |
| **ConversationParticipant** | Join row — lets one conversation have many participants (the "swarm"). |
| **ConversationMessage** | One immutable message, authored by one participant. |

There is exactly **one write path** for messages
(`POST /conversations/{id}/messages`, `IngestMessage`) — bridges own
delivery and call it after actually posting to the human-facing channel
(Slack, etc.). Never insert messages any other way; if the bridge didn't
deliver it, it isn't stored.

## Step 1 — Register the conversation

```
POST /conversations
{
  "bridgeId": "slack-prod",
  "participantIds": ["facilitator_uuid", "human_user_uuid"],
  "description": "Customer support thread"
}
```

A conversation is normally triggered by the **first inbound message** on
a channel (e.g. a Slack bridge resolving/registering it on first contact),
not by an explicit "start conversation" command — see
[bridge-integration.md](./references/bridge-integration.md). Start with
the minimum viable swarm (typically a facilitator + the human) and add
specialists later (Step 2) rather than registering every possible agent
upfront.

## Step 2 — Add or remove agents mid-conversation

```
POST   /conversations/{id}/participants           { "participantId": "expert_agent_uuid" }
DELETE /conversations/{id}/participants/{pid}
```

Typical trigger: a facilitator agent decides mid-conversation that a
specialist should join (escalation), and attaches it instead of talking
to it purely peer-to-peer — attaching puts it in the same shared thread
so every future turn includes its context automatically.

## Step 3 — Let the daemon drive turns (don't call agents directly from outside it)

A background daemon ticks (~1s interval) and, per tick, picks one
"ready" participant with no strict ordering guarantee across ticks — see
[daemon.md](./references/daemon.md) for the exact resolution/invocation
steps (role resolution, message windowing, calling the remote agent via
[6022-a2a-initiate](../6022-a2a-initiate/SKILL.md), and callback back to
the bridge). Don't build a second code path that calls participant agents
directly outside this loop — you'll fork message ordering/history.

- **Human** participants: the daemon skips them (nothing to invoke).
- **Agent**/**Facilitator** participants: the daemon resolves their ENS
  card, builds a turn from the windowed conversation history filtered for
  that participant, and invokes them via the same x402-payer flow as
  [6022-a2a-initiate](../6022-a2a-initiate/SKILL.md) — the orchestrator
  itself is the payer here, paying each participant agent it invokes.
- A `PaymentRequiredError` that can't be resolved is logged and the turn
  is skipped (the orchestrator doesn't retry-loop a broken payment) —
  other errors fail the turn outright.

## Step 4 — Conclusion

A facilitator signals it's done via an in-band marker in its reply (an
`:end:`-style marker in the current implementation); the conversation
gets marked concluded. Sending a new message re-opens a concluded
conversation automatically **unless** the new message's author is itself
an agent/facilitator (prevents an agent from ping-ponging a closed thread
with itself).

## Wiring a new channel/bridge

New bridges (beyond Slack) should:
1. Dedup inbound events (don't forward the same message twice).
2. Resolve or register the author as a `Participant` (human or agent).
3. Resolve-or-register the conversation for that channel thread (store a
   local `channel + threadId → conversationId` mapping the first time).
4. Call `IngestMessage` — never write directly to conversation storage.
5. Handle the callback (daemon → bridge) by posting to the human-facing
   channel, then calling `IngestMessage` again for the agent's reply —
   see [bridge-integration.md](./references/bridge-integration.md).

## Threading / memory across conversations

Which past context an agent brings into a given conversation is the
**agent's own responsibility**, not something the broker enforces — A2A
`SendMessage` carries an optional `contextId` to link to a prior thread,
but matching/retrieval logic lives in each agent framework's own memory
system (Hermes, OpenClaw, ...). See
[Memory Threading](https://docs.agentic.6022.io/docs/users/memory/threading-phase)
for the four-step model (conversation received → session identified →
memory retrieved → conversation continues) this skill assumes agents
implement on their own side.
