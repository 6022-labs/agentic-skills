---
name: mount-agent-node
description: Mount (deploy) a new agent-node onto existing shared infrastructure (database server, Vault, Kubo, Grafana stack). Use this whenever the user wants to deploy, mount, add, or spin up a new agent-node, agent runtime, or agentic-agent-node instance — even if they just say "add an agent for X", "deploy a new agent", or "clone an agent node". Handles per-agent database provisioning (postgres/mysql/mariadb), host-specific wiring via a host strategy (Railway today), and config copying from already-deployed nodes. Does NOT create shared services.
compatibility: Requires a Railway transport — MCP connector preferred, else the railway CLI logged in — and python3 (any OS — the DB provisioning script bootstraps its own driver).
argument-hint: "[service-name] [project] [environment] — all optional, asked tersely if omitted"
---

# Mount a new agent-node

Agent-node = one instance of `6022-labs/agentic-agent-node`, mounted beside **already-existing** shared infra (DB server: Postgres/MySQL/MariaDB, Vault, Kubo, Grafana stack). Never create or modify shared services — this skill adds one node only.

## Host strategy

Everything host-specific (commands, variable refs, links, discovery) lives in one file per host. Pick host → read its file → follow workflow with its commands.

| Host    | File                    |
| ------- | ----------------------- |
| Railway | `references/railway.md` |

Railway = only host today, use without asking. New host = new file; contracts below unchanged.

## Host-independent contracts

**Per-agent DB** on the shared server: db name = fresh lowercase GUID; user = GUID minus dashes (32 chars — fits MySQL user limit); password = random secret you generate.

**Per-node secrets.** `JWT__SIGNING_KEY` fresh per node (`openssl rand -hex 32`), never copied — shared key ⇒ one node can forge another's sessions.

**Vault token — never propagate a readable copy.** `VAULT__TOKEN` unlocks every agent's secrets ⇒ exempt from config copying. Only two sources: (1) host mechanism unreadable by design (e.g. Railway sealed shared variable wired by reference — host file), (2) user pastes it explicitly = their deliberate act + responsibility ⇒ summary must carry the host file's seal warning. A *readable* token found anywhere (shared or service-scoped) = refusal: stop before creating anything, failure summary + host file remediation. Never echo a token value — discovered or pasted.

**Fail fast — never improvise.** Anything unexpected (missing service, failed command, unreachable URL, wrong-shaped output) → stop, failure summary + one next action. A worked-around mount looks fine but diverges from every other node → untraceable debugging later. Only sanctioned deviation = one documented in the host file.

**Questions — never ask what you can determine.**

- Env has an agent-node → it's the config template. Ask only: project, environment, service name (+ driver if >1 DB server).
- No agent-node → chain set = business decision, ask first: "Which chains?" (default = sibling envs' chain list; the 6022 contracts must be deployed on every chosen chain — today Polygon Amoy, plus Base Sepolia for payments). Then ask RPC URL + payment facilitator URL for chosen chains + other non-derivables (Vault token, …). Offer sibling-env values as defaults.
- Testnet + mainnet chains on one node = nonsense — say so, user picks one side.
- Terse, batched: "Which RPC URL for chain 80002?" — no preamble, no why.

**Every user-facing line = instruction, not essay.** Questions, warnings, refusals, summaries: one imperative sentence each, exact next action (exact dashboard path / command / value). No background, no justification, no unasked options — extra words bury the action; buried action doesn't get followed. Host file templates = verbatim: fill placeholders, add nothing.

**Final summary — mandatory, exact, nothing more:**

```
✅ <service-name> mounted
Node:  https://<public-domain>
Host:  <dashboard-link-to-service>
DB:    <driver> <db-name>
```

Failure → replace ✅ with the failure + one next action. No recap of steps.

## Workflow

0. **Self-update** — canonical: `https://raw.githubusercontent.com/6022-labs/agentic-skills/main/skills/mount-agent-node/SKILL.md` (same pattern for other files). Fetch + compare; different → follow upstream this mount, overwrite stored copy if writable. Unreachable → one line, skip (only fail-fast exemption).
1. **Inputs** — ask tersely: project, environment, service name. List candidates via host CLI first so user picks, not types.
2. **Discover** — enumerate env services (host file). Identify: shared DB server(s), Vault, Kubo (may be absent — external IPFS node instead, host file), telemetry collector, template agent-node. Resolve Vault-token state now (host file) — token refusal fires before anything is created.
3. **Database** — driver: one server → use it; several → ask. Provision:
   ```bash
   python3 scripts/provision_agent_db.py <postgres|mysql|mariadb> "<admin-public-url>"
   ```
   Creates db + user + grants, prints `DB_NAME`/`DB_USER`/`DB_PASSWORD`. No hand-rolled SQL — script encodes the sharp edges (postgres `CREATE DATABASE` refuses transactions; grants run connected to the new db). Server unreachable → report URL, stop — no proxies, tunnels, `railway run`/SSH, docker, or manual SQL.
4. **Variables** — full set per host file: reference templates + template-node copies + fresh `JWT__SIGNING_KEY` + DB URL. No template node → question policy.
5. **Create & deploy** — service from repo `6022-labs/agentic-agent-node`, **all variables at creation** (unconfigured boot = crash-loop), attach public domain, enable host sleep/serverless mode (idle node stops billing).
6. **Verify** — poll deploy → SUCCESS (build takes minutes), then `curl -s https://<domain>/` expect 200 (web console; `/.well-known/6022` 404s `draft_not_found` by design until an agent registers). Failure → service logs; usual culprits: missing variable, bad DB URL (test via the server's public proxy).
7. **Report** — the summary above.

## Cleanup on failure

No orphans: drop the just-created db + user (`DROP DATABASE` / `DROP ROLE` or `DROP USER`), or tell the user exactly what was created.
