# agentic-skills

Agent skills for the [6022 Agentic Team protocol](https://docs.agentic.6022.io).

Each directory under `skills/` is a self-contained skill (`SKILL.md` + bundled scripts and references) in the cross-agent format used by Claude Code, OpenClaw, Codex CLI, and others. `.claude-plugin/` additionally makes the repo a Claude Code plugin marketplace.

## Skills

| Skill | What it does |
|-------|--------------|
| [`mount-agent-node`](./skills/mount-agent-node) | Mount a new agent-node onto existing shared infra (DB server, Vault, Kubo, Grafana). Host-agnostic workflow, Railway today. |
| [`self-mint-and-ens-registry`](./skills/self-mint-and-ens-registry) | Create an agent's 6022 identity: wallet, gas request, identity NFT mint, ENS profile — verified addresses and ABIs bundled. |
| [`serve-agent-endpoints`](./skills/serve-agent-endpoints) | Make a runtime reachable: signed `/.well-known/*` discovery documents and a live `POST /a2a`. Ships a verifier that proves it, rather than assuming it. |
| [`price-agent-access`](./skills/price-agent-access) | Charge other agents for calls: payment policy and x402 rules, address-scoped tiers, zero-price identification. |
| [`call-agent-a2a`](./skills/call-agent-a2a) | Call another agent and pay its x402 challenge as the caller — ENS discovery, card verification, EIP-3009/Permit2 signing. |
| [`orchestrate-agent-swarm`](./skills/orchestrate-agent-swarm) | Run several agents on one thread through the conversation broker: bridges, participants, attach/detach, daemon-driven turns. |

Each skill is self-contained and states its own boundaries: identity lives in
`self-mint-and-ens-registry`, reachability in `serve-agent-endpoints`, the payer
side in `call-agent-a2a`, the payee side in `price-agent-access`. Where a skill
bundles a script, the script is the deterministic path — the prose explains what
it did, not how to reimplement it.

## Install

**Claude Code (recommended):**

```
/plugin marketplace add 6022-labs/agentic-skills
/plugin install agentic@agentic-skills
```

Skills appear as `agentic:<skill-name>`. Update with `/plugin marketplace update agentic-skills`.

**Any other runtime:** copy (or symlink, to track `git pull`) `skills/<skill-name>` into your runtime's skills directory — e.g. `~/.claude/skills/` for a manual Claude Code install.

If a skill bundles `scripts/requirements.txt`, `pip install -r` it once.

## Contributing

Add a directory under `skills/` named for the **action it performs**
(`mount-agent-node`, `call-agent-a2a`), not for a noun or a namespace. It needs:

- `SKILL.md` — frontmatter (`name`, `description`, optionally `compatibility` and
  `argument-hint`) and a body under ~200 lines. Open with a **step 0 self-update**
  block pointing at the canonical `raw.githubusercontent.com` URL, so a copy
  installed elsewhere can notice it is stale.
- `scripts/` — anything deterministic. If a step can be gotten wrong silently
  (a signature, an address, an EIP-712 domain), it belongs in a script with a
  documented exit-code contract, not in prose.
- `references/` — the detail the body links out to, one file per topic.
- `evals/evals.json` — prompts plus objectively checkable assertions.
- Verified data (addresses, ABIs) in bundled files, never inline in prose.

Then add it to the table above and open a PR.

## License

MIT — see [LICENSE](LICENSE).
