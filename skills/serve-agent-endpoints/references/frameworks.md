# Wiring an external runtime — observed failure modes

An agent built and hosted outside 6022 keeps its own runtime, memory and tooling;
6022 adds an identity and interop layer on top. Nothing here asks a framework to
be rewritten. What follows is the set of problems that actually showed up when
external runtimes were connected — grouped by cause rather than by framework,
because every one of them recurs across frameworks.

The frameworks these were observed against: **Grok bot** (the most live interop
testing so far), **Hermes** (Nous Research — persistent memory, portable
runtime), **OpenClaw** (local-first, personal-device hosted). Where a note is
inference from a framework's public description rather than tested interop, it
says so.

## The origin cannot host a live endpoint

**Symptom:** discovery documents fetch fine, `POST /a2a` 404s or times out.

**Cause:** the ENS `url` points at a static host that serves files only.

**Fix:** the gate-in-front pattern in
[`a2a.md`](./a2a.md#serving-a-live-endpoint-behind-a-static-origin). Keep the
static host for `/.well-known/*` and put a small always-on process in front for
the live routes. Do not try to make the static host smarter, and do not invent a
second endpoint or product to route around it.

This was Grok bot's shape exactly. It is also the likely shape for **OpenClaw**
on a personal device behind NAT: if reachability is intermittent, an always-on
relay that queues turns and wakes the runtime is the honest answer — advertising
an endpoint that times out most of the time is worse than advertising nothing,
because callers keep routing to it.

## The gate answers instead of the runtime

**Symptom:** `200` with plausible-looking text, and it passes a naive
reachability check.

**Cause:** the relay process returns a canned reply, a regex-matched
acknowledgement, or a hardcoded string, and the real runtime is never invoked.

**Why it survives so long:** every status-code-level check passes. Only content
inspection catches it.

**Fix:** `scripts/verify_node.py` sends a random nonce and requires it back. A
gate that fabricates replies cannot produce a nonce it has never seen. This is
why the verifier's A2A check is a content check, not a status check.

## A 402 loop that never resolves

**Symptom:** the caller signs a valid payment, retries, and gets `402` again,
forever.

**Cause:** in every observed instance, an intermediate proxy stripping the
`PAYMENT-SIGNATURE` / `PAYMENT-REQUIRED` headers — not a malformed x402 payload.
Static hosts and their CDNs commonly drop unknown headers.

**Fix:** configure the proxy to pass them through. That is the actual repair.

If the proxy is genuinely outside your control, a gate may accept the payload
from the request body as a local fallback — but treat that as a private
workaround between you and your own proxy, not as protocol. It is not part of
x402, other agents will not send it, and it must never be advertised in a
discovery document. Disabling payment gating to "fix" the loop is not an option;
it makes the node free without saying so.

## An inbox published as A2A

**Symptom:** callers get acknowledgements, never answers.

**Cause:** an existing contact form, chat widget, or human-read webhook was
advertised as the A2A endpoint because it accepts `POST` and returns `200`.

**Fix:** see [`a2a.md`](./a2a.md#an-inbox-is-not-a2a). Do not point ENS at it.
Publish only after the verifier passes.

## A stale origin after a redeploy

**Symptom:** the agent works, and is unreachable through 6022 discovery.

**Cause:** the ENS `url` record still points at the previous host. Discovery
starts at ENS, so a correct runtime at a new address is invisible.

**Fix:** treat the origin as **derived state, never a constant**. Re-derive and
re-publish the `url` record on every redeploy, and re-run the verifier. A
hardcoded origin in a memory store or a prompt template is the same bug with a
longer fuse.

This is the primary risk for **Hermes**, whose portable runtime is designed to
move between hosts. Its memory layer should durably hold the identity —
collection instance address, token id, wallet address — and re-derive the origin
each boot rather than persisting it. (Inferred from Hermes's public description;
confirm the exact memory-layer hooks before shipping.)

## Minting a second identity for an agent that already has one

**Symptom:** two NFTs, two ENS names, ambiguous provenance.

**Cause:** assuming a fresh mint is always step one.

**Fix:** if the framework already controls an EVM address — **OpenClaw**'s
attestation model is the case in point — register that address on the existing
agent token with `addAgentAddress` rather than minting again. Only mint when
there is genuinely no prior on-chain identity to attach to. See
`self-mint-and-ens-registry/references/flow.md` for the ownership model and the
post-mint mutation calls.

## Before telling an owner the integration is live

The rule is the same for every framework, gated or not, static or dynamic:
`scripts/verify_node.py` exits 0, against the same origin the ENS record points
at, including a real payment round-trip if the node is priced. "It deployed
successfully" is not the same claim and has repeatedly not been true.
