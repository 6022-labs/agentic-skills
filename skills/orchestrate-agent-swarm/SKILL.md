---
name: orchestrate-agent-swarm
description: >-
  Run a conversation with several 6022 agents on one thread (a swarm), through
  the orchestrator's conversation broker: register a bridge and its participants,
  open a conversation, attach or detach agents mid-thread, ingest messages, and
  let the background daemon decide who speaks next and invoke each agent over
  A2A. Use this whenever someone wants to "start a conversation between agents",
  "add an agent to a conversation", "trigger a swarm", "orchestrate multiple
  agents on one thread", "escalate to a specialist agent", "wire Slack (or
  another channel) to agents", or asks why an agent is not taking its turn.
  Calling a single agent directly is `call-agent-a2a`; this is N agents sharing
  one thread with a daemon driving turns. Docs: https://docs.agentic.6022.io
compatibility: A running agent-swarm conversation broker (agentic-orchestrator), and a bridge that owns delivery to the human-facing channel.
argument-hint: "[bridge] [participants] [description] — the thread to open and who is in it"
---

# Orchestrate a multi-agent conversation

`call-agent-a2a` is one caller reaching one callee. This is **N agents on one
thread**, where nobody calls anybody directly: participants join a conversation,
and a background daemon decides who speaks next and invokes them.

That inversion is the whole design. It is also the rule most easily broken —
see "the two write paths you must not fork" below.

## Step 0 — self-update

Canonical source:
`https://raw.githubusercontent.com/6022-labs/agentic-skills/main/skills/orchestrate-agent-swarm/SKILL.md`
(same URL pattern for every file). Fetch, compare, follow upstream this run if
they differ, overwrite your stored copy if writable. Unreachable → one line,
continue.

## The model

| Concept | What it is |
|---------|------------|
| **Bridge** | the channel integration that owns delivery (Slack today). Registered once; identified by a **UUID** |
| **Conversation** | one thread, tied to one bridge. Has a description and a status; can be concluded and re-opened |
| **Participant** | a human (display name) or an agent (on-chain identity). Global, not per-conversation |
| **ConversationParticipant** | the join row — one conversation, many participants: the swarm |
| **ConversationMessage** | one immutable message, authored by one participant |

Participants are **global rows reused across conversations**, which is why they
are registered separately and then attached. Registering a participant per
conversation would give the same agent several identities and split its history.

## The two write paths you must not fork

1. **Messages enter only through `POST /conversations/{id}/messages`.** The
   bridge calls it *after* it has actually posted to the human-facing channel.
   The point is that "the human saw it" and "it is in the conversation" cannot
   drift apart — if the bridge did not deliver it, it is not stored.

2. **Agents are invoked only by the daemon.** Do not add a second path that
   calls participant agents directly from outside the loop. You will fork
   message ordering and history, and the symptom (an agent replying to context
   it should not have, or missing context it should) appears far from the cause.

If you need an agent to answer *outside* a conversation, that is `call-agent-a2a`
— a different thing, deliberately.

## Step 1 — register the bridge (once)

```jsonc
POST /bridges
{ "id": "3f2c…uuid",  "kind": "slack",  "baseUrl": "https://slack-bridge.example.com" }
```

`kind` must be a known bridge kind (`slack` is the only one today). `baseUrl` is
where the daemon posts completions back. `id` is optional — supply one to make
registration idempotent across restarts (the Slack bridge does exactly this,
carrying its `bridge_id` in config), or omit it to have the broker generate one.

**`bridgeId` is a UUID, not a name.** `"slack-prod"` is rejected by validation.
Register the bridge and keep the UUID it returns; everything downstream refers to
it by that.

## Step 2 — register participants

```jsonc
POST /participants/agents   { "collectionAddress": "0x…", "collectionTokenId": "42" }
POST /participants/users    { "displayName": "Pierre" }
```

An agent participant is identified by **its on-chain identity** — collection
address plus token id — not by its ENS domain. The ENS domain is derived from
that identity and stored alongside; the daemon uses it to resolve the agent's
card at invocation time. Identifying by ENS would break the moment a name
changed.

`collectionTokenId` is a string carrying an unsigned integer (it exceeds what
JSON numbers safely represent).

An agent's role — `clone`, `human`, `expert`, `facilitator` — is not set here. It
is read from the `role` attribute on its NFT, and anything unrecognized falls
back to `expert`.

## Step 3 — open the conversation

```jsonc
POST /conversations
{
  "bridgeId": "3f2c…uuid",
  "participantIds": ["facilitator-uuid", "human-uuid"],
  "description": "Customer support thread"
}
```

Only `bridgeId` is required. In practice a conversation is **triggered by the
first inbound message on a channel** — the bridge resolves-or-registers it on
first contact — rather than by an explicit "start a conversation" command. See
`references/bridge-integration.md`.

Start with the minimum viable swarm, typically a facilitator plus the human, and
let specialists be attached later. Registering every possibly-relevant agent
upfront means every one of them consumes turns, and the orchestrator pays each
one it invokes.

## Step 4 — attach and detach mid-thread

```
POST   /conversations/{id}/participants            { "participantId": "expert-uuid" }
DELETE /conversations/{id}/participants/{participantId}
GET    /conversations/{id}/participants
```

The typical trigger is escalation: a facilitator decides a specialist is needed
and attaches it, rather than calling it peer-to-peer. Attaching puts it in the
shared thread, so every later turn carries its context automatically — a
peer-to-peer call would leave the rest of the swarm unaware the exchange happened.

## Step 5 — let the daemon drive turns

A background daemon ticks about once a second and picks one *ready* participant
per tick, with **no ordering guarantee across ticks**. Per turn it resolves the
participant's role, skips humans (there is nothing to invoke), builds a windowed
history filtered for what that participant should see, resolves its ENS card, and
invokes it over A2A — paying its x402 challenge, because **the orchestrator is
the payer for every agent it invokes**.

An unresolvable payment error is logged and the turn is **skipped**, never
retried in a loop. Other errors fail the turn. Full sequence in
`references/daemon.md`.

Do not build logic that assumes agent A replies before agent B, or that a
participant gets exactly one turn per tick. If you need strict ordering, encode
it as explicit state on the conversation — not as an assumption about scheduling.

Every attached agent should already pass `serve-agent-endpoints`' verifier. A
participant whose node is not actually live produces silently skipped turns,
which reads as "the agent is quiet" rather than "the agent is broken" — one of
the more expensive failure modes here.

## Step 6 — conclusion

A facilitator signals it is done with an in-band marker in its reply (`:end:` in
the current implementation) and the conversation is marked concluded. An operator
can force it with `POST /conversations/{id}/force-conclude`.

A new message re-opens a concluded conversation automatically — **unless its
author is itself an agent or facilitator.** That exception is what stops an agent
from ping-ponging a closed thread with itself forever.

## Memory across conversations

What past context an agent brings into a thread is the **agent's own
responsibility**. A2A carries an optional `contextId` to link to a prior thread,
but retrieval lives in each framework's memory system; the broker enforces
nothing. See
https://docs.agentic.6022.io/docs/users/memory/threading-phase.

## When to go deeper

| Question | Where |
|----------|-------|
| Every broker route and its body | `references/broker-api.md` |
| The daemon's per-turn sequence, locking, ordering | `references/daemon.md` |
| Writing a new bridge, dedup, callbacks | `references/bridge-integration.md` |
| The x402-payer flow the daemon uses | skill `call-agent-a2a` |
| Making a participant agent actually reachable | skill `serve-agent-endpoints` |
