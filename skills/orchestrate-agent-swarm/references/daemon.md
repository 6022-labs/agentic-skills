# Conversation Processor Daemon

Grounded in `agentic-orchestrator/agent-swarm/src/conversation_broker/services/`:
`conversation_processor_daemon.go` + `conversation_processor.go`.

## Daemon loop

```
Start(ctx):  spawn a background ~1-second ticker
Stop():      wait for in-flight work, then cancel

runLoop(ctx):
  for tick := range ticker.C {
    processNextParticipant(ctx)   # no ordering guarantee across ticks
  }
```

There's one in-process lock per conversation — fine for a single
replica, but running multiple orchestrator instances needs a real
(e.g. Postgres advisory) lock instead, or two replicas can both try to
process the same participant on overlapping ticks.

## `ProcessConversation(participant, conversation)` — one turn, one participant

1. **Resolve role** — if `Human`, return immediately (nothing to invoke).
2. **Fetch the bridge** — needed for the callback URL/kind (Slack, etc.).
3. **Fetch processable messages** for this participant — filtered by
   role (Human/Agent/Facilitator have different visibility rules) and by
   conversation.
4. **Resolve the agent's ENS domain → fetch its `/.well-known/6022` card**
   (cached ~300s — don't refetch on every tick for the same agent).
5. **Build the turn input**:
   - the participant's own instructions (role template, bounded length)
   - windowed conversation history, filtered for what this participant
     should see
   - other participants' names/roles, so an agent knows who else is in
     the room
6. **Invoke the remote agent** via the same x402-payer flow as
   [call-agent-a2a](../../call-agent-a2a/SKILL.md) — the orchestrator is
   the payer for every agent it invokes this way.
7. **Payment errors**: a `PaymentRequiredError` that can't be resolved is
   logged and the turn is skipped, not retried in a loop. Any other error
   fails the turn.
8. **Post-process the completion**: demojize text, check for the
   facilitator's conclusion marker (`:end:`-style) that ends the
   conversation.
9. **Callback to the bridge**: posts the completion to the bridge's
   registered `baseUrl` (the Slack bridge receives it at
   `POST /broker/callback`) — the
   bridge posts it to the human-facing channel (Slack, etc.) and then
   calls `IngestMessage` to persist it (single write path, see
   [bridge-integration.md](./bridge-integration.md)).

## Why "no ordering guarantee" matters

Don't design a flow that assumes agent A always replies before agent B in
a given tick, or that a given participant gets exactly one turn per tick.
If your orchestration logic needs strict turn order, encode that as an
explicit state machine on the conversation (e.g. a "whose turn" field),
not as an assumption about daemon scheduling.
