# Self-Check: Mint + Node Operational (Automated, Re-runnable)

One routine, re-run any time — after initial setup, after a redeploy,
after a wallet/key rotation, or periodically as a health check. It
combines the checks scattered across this skill into a single pass/fail
report, instead of a one-time manual verification.

## Routine

Run these checks **in order**; stop at the first failure and report it —
don't report "OK" if a later check wasn't actually reached.

### 1. Mint confirmed on-chain

- Read the token's owner from the **collection instance** contract (never
  the implementation — see
  [contracts.md](./contracts.md#implementation-vs-collection-instance--do-not-mint-into-the-implementation)).
- **PASS**: owner matches the agent's wallet address, token exists.
- **FAIL**: no token, or owner mismatch — the mint either didn't happen or
  was reconciled to the wrong wallet. Re-run
  [registration-flow.md](./registration-flow.md) mint/check step, don't
  proceed to later checks.

### 2. Well-known documents reachable and signed correctly

- `GET <url>/.well-known/6022` and `GET <url>/.well-known/agent-card.json`.
- **PASS**: both return 200, `protocol`/`name`/`endpoints` fields are
  populated (not placeholders), and the signature verifies against the
  `evm` address from check 1 (same wallet that owns the token).
- **FAIL**: 404/500 (not deployed or crashing), signature doesn't verify
  (wrong key, or JCS-canonicalization bug), or an advertised
  `endpoints`/`exchange` URL that isn't actually this same origin.

### 3. `/a2a` is live, not a stub or an inbox

This is the [loop-test](./a2a-protocol.md#loop-test-verifying-a-live-a2a-integration-end-to-end),
re-run as part of this routine rather than as a one-off:

1. `POST <url>/a2a` without payment → expect `402` with `PAYMENT-REQUIRED`
   header present.
2. Retry with a signed payment from an accepted wallet, prompt exactly
   `Reply with exactly PAID_V2_OK.` → expect `200` and
   `result.message.parts[0].text === "PAID_V2_OK"`, produced by the real
   runtime (not a canned/regex reply — see
   [a2a-protocol.md § inbox is not A2A](./a2a-protocol.md#inbox-is-not-a2a)).
- **PASS**: both steps behave exactly as above.
- **FAIL** (with specific causes to report, not just "failed"):
  - `404`/HTML homepage → `/a2a` isn't deployed at that origin.
  - `200` immediately, no `402` → payment gating isn't wired, or price is
    `"0"` and that's expected (confirm which before treating as a bug).
  - `402` but no `PAYMENT-REQUIRED` header → see
    [x402-payments.md § wire transport](./x402-payments.md#wire-transport-headers-not-just-json-body),
    likely a proxy stripping headers.
  - `200` with wrong/generic text → the gate answered instead of the real
    runtime, or a proxy is caching a stale response.

### 4. ENS points at the origin that just passed check 3

- Resolve the agent's ENS name via CCIP-Read (see
  [ens-ccip-read.md](./ens-ccip-read.md)) and confirm the `url` text
  record equals the origin just checked.
- **PASS**: matches.
- **FAIL**: ENS points at a different/old origin (e.g. after a redeploy),
  or isn't registered yet — don't consider the node "live" for discovery
  purposes even if checks 1-3 all passed.

## Report format

Produce a short structured result, one line per check, e.g.:

```
mint:        PASS  (owner 0xabc... , token 42, collection 0xdef...)
well-known:  PASS  (both signed, endpoints match origin)
a2a:         FAIL  (402 present, no PAYMENT-REQUIRED header — proxy strips it)
ens:         SKIP  (blocked by a2a failure, not checked)
```

Overall status is **OK** only if every check that ran is `PASS`. Any
`FAIL` means the node is not operational for discovery/payment purposes,
regardless of how far the mint got — this mirrors the
["a node is four things"](../SKILL.md#a-node-is-four-things) rule.

## When to re-run

- Immediately after Step 1 (mint) and Step 3 (A2A) of the main flow.
- After any redeploy, key rotation, or DNS/host change for the origin.
- Periodically (e.g. daily) if the agent is meant to stay discoverable
  long-term — a proxy update or expired cert can silently break check 2/3
  without anyone noticing until another agent tries to call in.
