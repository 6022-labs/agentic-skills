# agentic-skills

Agent skills for the [6022 Agentic Team protocol](https://docs.agentic.6022.io).

Each directory under `skills/` is a self-contained skill (`SKILL.md` + bundled scripts and references) in the cross-agent format used by Claude Code, OpenClaw, Codex CLI, and others. `.claude-plugin/` additionally makes the repo a Claude Code plugin marketplace.

## Skills

| Skill | What it does |
|-------|--------------|
| [`mount-agent-node`](./skills/mount-agent-node) | Mount a new agent-node onto existing shared infra (DB server, Vault, Kubo, Grafana). Host-agnostic workflow, Railway today. |
| [`self-mint-and-ens-registry`](./skills/self-mint-and-ens-registry) | Create an agent's 6022 identity: wallet, gas request, identity NFT mint, ENS profile — verified addresses and ABIs bundled. |

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

Add a directory under `skills/` with a `SKILL.md` (frontmatter: `name`, `description`); deterministic logic in `scripts/`, docs in `references/`, verified data (addresses, ABIs) in bundled files — never hard-coded in prose. Add it to the table above and open a PR.

## License

MIT — see [LICENSE](LICENSE).
