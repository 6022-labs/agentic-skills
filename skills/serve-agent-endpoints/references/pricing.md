# Pricing access with x402

The gate that turns an answering agent into a paid one. It sits on the **same
routes** this skill already serves — `/responses` and `/a2a` share one access
chain — so pricing an agent prices every way of talking to it. You cannot charge
for one and give away the other.

It is also why `paymentMethods` appears in `/.well-known/6022`: that field is
derived by listing the agent's payment rules and taking the distinct sorted
method names. Configure no rules and the field is omitted; configure one and
callers are told to expect a 402 before they ever send a turn.

## The one rule that prevents a broken price

**Never set `assetTransferMethod`, `eip712Name`, or `eip712Version` yourself.**
They belong to the token, not to you. The runtime reads them off the asset
on-chain when the rule is saved, precisely because a guessed EIP-712 domain
produces a digest that recovers to a stranger's address — callers would sign
correctly and be rejected forever, with no error that points at the cause.

Send `network`, `asset`, `payTo`, and `addressPatterns`. The runtime fills in the
rest and returns the sanitized config. If you feel the urge to type
`"assetTransferMethod": "eip3009"`, that is the skill missing a step, not you
needing to help.

## The endpoints

All under the gateway's public prefix (`/api` by default) and **authenticated** —
these are operator routes, not the public discovery surface:

```
GET    /config/payment-policy              # the policy head
DELETE /config/payment-policy              # remove the policy and its rules
GET    /config/payment-policy/rules        # list rules, in evaluation order
POST   /config/payment-policy/rules        # add a rule
PUT    /config/payment-policy/rules/{id}   # update a rule
DELETE /config/payment-policy/rules/{id}   # remove a rule
```

A `409` on the policy before the agent is minted is expected, not a fault: the
runtime refuses to materialize rules for an identity that does not exist yet.
Until then the gate runs ruleless and x402 still advertises a 402.

## A rule

```jsonc
POST /api/config/payment-policy/rules
{
  "name": "standard turn",
  "price": "10000",
  "method": "x402",
  "config": {
    "network": "eip155:80002",
    "asset": "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582",
    "payTo": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "addressPatterns": [".*"]
  }
}
```

| Field | Rule |
|-------|------|
| `name` | 1–64 characters |
| `price` | a **non-negative integer string in the asset's atomic units** — `"10000"` of a 6-decimal USDC is 0.01 USDC. Not a decimal, not a float |
| `method` | defaults to `x402`, the only method the runtime defines today |
| `config.network` | CAIP-2 (`eip155:<chainId>`). The node must have an RPC for that chain — it needs one to inspect the asset |
| `config.asset` | the ERC-20 to be paid in. Must be a real ERC-20 the node can read |
| `config.payTo` | who receives the funds. Usually the agent's own wallet, but a treasury address is equally valid |
| `config.addressPatterns` | at least one regex, matched against the caller's **lowercase 0x address**. `".*"` opens the rule to everyone |

A rule with a non-zero price also requires a configured **facilitator** for its
network — the facilitator is what actually settles, so a paid rule without one
would take signatures and never collect.

## Rules are a table, evaluated cheapest-match-first

Rules carry a position and the gate picks the **cheapest rule whose
`addressPatterns` match the caller**. That makes tiering a matter of adding
rows, not writing logic:

```jsonc
// partners call free, everyone else pays
{ "name": "partners", "price": "0",     "config": { …, "addressPatterns": ["^0xabc…$", "^0xdef…$"] } }
{ "name": "public",   "price": "10000", "config": { …, "addressPatterns": [".*"] } }
```

Because the cheapest match wins, a broad `".*"` rule priced below a narrow one
makes the narrow one unreachable. When a rule seems to be ignored, compare prices
before suspecting the patterns.

## Zero price means "identify yourself", not "no payment"

A `"0"` rule still makes the caller **sign** an authorization — it is simply never
settled on-chain. The signature is what establishes *which wallet* is calling,
which is the only reason address-scoped free access can exist at all.

So a free agent and an unpriced agent are different things:

| | Unpriced (no rules) | Zero-price rule |
|---|---|---|
| caller sees | a direct answer | `402`, then answers after signing |
| callee learns | nothing about the caller | the caller's verified wallet address |
| `paymentMethods` in `/.well-known/6022` | omitted | `["x402"]` |

If you want to know who is calling without charging them, that is a zero-price
rule. If you want no friction at all, define no rules.

## What a caller experiences

1. Calls `/a2a` or `/responses` with no payment → `402` plus a `PAYMENT-REQUIRED`
   header listing the matching rules as `accepts[]` options.
2. Signs one option with its wallet and retries with `PAYMENT-SIGNATURE`.
3. The gate recovers the payer, checks it against the matched rule's patterns,
   verifies with the facilitator, runs the turn, and settles **after** the
   response is delivered.

That ordering is why a failed turn must return a non-2xx status: settlement is
tied to success, so a `200` wrapping an error would charge for nothing. If you
are implementing the endpoints yourself, see this skill.

There is also a free-access shortcut ahead of x402 in the chain: a caller
presenting a valid JWT whose address matches a zero-price rule is let through
without the 402 round-trip. It changes the handshake, not who is entitled.

## Verify from the outside

An operator's view of the config is not proof that callers can pay. Check it the
way a caller does:

```bash
# expect 402 with a PAYMENT-REQUIRED header
curl -si -X POST https://agent.example.com/api/a2a \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"messageId":"1","role":"user","parts":[{"text":"hi"}]}}}' \
  | head -20

# then complete the round-trip with a funded wallet
python ../../call-agent-a2a/scripts/a2a_call.py --target https://agent.example.com \
       --message "hi" --private-key-env AGENT_PRIVATE_KEY
```

A `402` whose `accepts[]` is empty means no rule matched the caller — usually
`addressPatterns` narrower than intended. A `402` that repeats after a correctly
signed retry is almost always a proxy stripping the payment headers; see
`call-agent-a2a/references/x402-payer.md`.
