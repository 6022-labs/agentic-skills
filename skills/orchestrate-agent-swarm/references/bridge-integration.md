# Wiring a Channel/Bridge (e.g. Slack)

Grounded in `agentic-orchestrator/channels-and-integrations/slack-bridge/src/slack_bridge_external/services/broker_message_forwarder.go`
(`BrokerMessageForwarder.Forward`).

## Trigger

An inbound message on the external channel (e.g. a Slack message in a
channel/thread) — **not** an explicit "start conversation" API call.

## Flow

```
1. Dedup check — has this exact event already been forwarded to the broker?

2. Resolve the author as a Participant:
   - Slack human user  → find-or-register SlackUser → brokerParticipantId
   - Slack agent/bot   → find SlackAgent → brokerParticipantId

3. resolveOrRegisterConversation(channel, parentMessageTs, author):
   - If this Slack thread already has a stored link to a broker conversation,
     reuse that conversationId.
   - If it's a NEW thread:
       RegisterConversation({
         bridgeId: <the bridge's UUID, from POST /bridges — not the string "slack">,
         participantIds: [facilitator, human_user],
       })
       store link: (channel, parentMessageTs) → conversationId
   - Return the conversationId either way.

4. IngestMessage(conversationId, authorParticipantId, content)
   — the bridge is the one calling this; the broker never inserts
   messages on its own initiative.

5. The daemon picks up the facilitator on its next tick and invokes it
   (see [daemon.md](./daemon.md)).

6. Facilitator's completion comes back via the bridge callback:
   - Bridge posts the completion to the Slack thread.
   - Bridge calls IngestMessage again to persist the agent's reply.

7. The facilitator can bring in more agents by:
   - Calling them directly, caller-side A2A (see
     [call-agent-a2a](../../call-agent-a2a/SKILL.md)), or
   - Attaching them to the conversation
     (`POST /conversations/{id}/participants`) so the daemon picks them
     up on a future tick and they see the shared thread history.
```

## Rules for any new bridge

- **Dedup first** — external channels routinely redeliver events; check
  before doing anything else.
- **Never bypass `IngestMessage`** — it's the single write path
  specifically so "was this message actually delivered to the human-facing
  channel" and "is it stored in the conversation" can't drift apart.
- **Store the thread→conversation mapping yourself** — the broker doesn't
  know about Slack-specific concepts like `parentMessageTs`; that mapping
  is the bridge's responsibility to persist and look up.
- **Register the minimum viable participant set** on first contact
  (typically facilitator + human) — let the facilitator attach specialists
  later rather than the bridge guessing which agents are relevant upfront.
