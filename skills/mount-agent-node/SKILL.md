---
name: mount-agent-node
description: Mount (deploy) a new agent-node onto existing shared infrastructure (database server, Vault, Kubo, Grafana stack). Use this whenever the user wants to deploy, mount, add, or spin up a new agent-node, agent runtime, or agentic-agent-node instance — even if they just say "add an agent for X", "deploy a new agent", or "clone an agent node". Handles per-agent database provisioning (postgres/mysql/mariadb), host-specific wiring via a host strategy (Railway today), and config copying from already-deployed nodes. Does NOT create shared services.
compatibility: Requires a Railway transport — MCP connector preferred, else the railway CLI logged in — and python3 (any OS — the DB provisioning script bootstraps its own driver).
argument-hint: "[service-name] [project] [environment] — all optional, asked tersely if omitted"
---

# Mount a new agent-node

An agent-node is one instance of the `6022-labs/agentic-agent-node` runtime. It always runs next to **shared infrastructure that already exists** — a database server (Postgres, MySQL, or MariaDB), Vault, Kubo (IPFS), and the Grafana observability stack. Never create or modify those shared services: this skill only adds one agent-node beside them.

## Host strategy

The workflow below is host-independent. Everything host-specific (CLI commands, variable references, dashboard links, service discovery) lives in one reference file per host. Pick the host, read its file, then follow the workflow using its commands:

| Host    | Implementation          |
| ------- | ----------------------- |
| Railway | `references/railway.md` |

Railway is the only implementation today — use it without asking. When AWS/GCP/Hetzner support lands, a new file here is the only extension point; the contracts below don't change.

## Host-independent contracts

**Per-agent database.** Every agent owns a dedicated database on the shared DB server:

- database name = fresh lowercase GUID (unique)
- user/role = the GUID without dashes (32 chars — fits MySQL's 32-char user-name limit)
- password = random secret you generate

**Per-node secrets.** `JWT__SIGNING_KEY` is generated fresh per node (`openssl rand -hex 32`), never copied — a shared key would let one node forge another's sessions.

**Vault token — never propagate a readable copy.** `VAULT__TOKEN` unlocks every agent's secrets in the shared Vault, so it is exempt from config copying. A new node may receive it in exactly two ways: through a host mechanism that is unreadable by design (e.g. Railway's sealed shared variable, wired by reference — see the host file), or as a value the user passes explicitly in the conversation — that is their deliberate act and their responsibility, and the summary must then carry the host file's seal warning. Discovering a *readable* token (shared or service-scoped) is a refusal case: stop before creating anything and report it via the failure summary with the host file's remediation. Never echo a token value — discovered or user-passed — back into the conversation.

**Fail fast — never improvise around the unexpected.** When anything deviates from what this skill expects (a service missing, a CLI command failing, a URL unreachable, output in the wrong shape), stop and report it via the failure summary with the one next action — don't invent a workaround. A hacked-around mount looks successful but is inconsistent with every other node, and that inconsistency surfaces later as a debugging nightmare nobody can trace back to the improvisation. The only sanctioned deviation is one explicitly documented in the host reference file (e.g. a known CLI bug with its prescribed fallback).

**Question policy — never ask what you can determine.**

- The target environment already has an agent-node → it is your config template. Copy its values; ask only: project, environment, service name (+ DB driver if the environment has more than one DB server).
- The environment has **no** agent-node → the **chain set is a business decision, ask it first**: "Which chains?" with the sibling environments' chain list as default (the 6022 contracts must be deployed on every chain chosen — today that's Polygon Amoy only plus Base Sepolia for payments; more will come). Then ask RPC URL + payment facilitator URL only for the chosen chains, plus the other non-derivable values (Vault token, …). Offer values found in sibling environments as defaults.
- **Mixing testnet and mainnet chains on one node makes no sense** — if the requested chain set spans both (e.g. 80002 Polygon Amoy testnet + 137 Polygon mainnet), say so and make the user pick one side before continuing.
- Questions are terse and direct: "Which RPC URL for chain 80002?" — no preamble, no explanation of why you ask. Batch related questions into a single prompt.

**Final summary — mandatory, terse, nothing more.** After a successful mount, report exactly:

```
✅ <service-name> mounted
Node:  https://<public-domain>
Host:  <dashboard-link-to-service>
DB:    <driver> <db-name>
```

If something failed, replace ✅ with the failure and the one next action. No verbose recap of steps taken.

## Workflow

0. **Self-update** — this copy may be stale. Canonical: `https://raw.githubusercontent.com/6022-labs/agentic-skills/main/skills/mount-agent-node/SKILL.md` (same pattern for the other files). Fetch and compare; if different, follow upstream for this mount and overwrite your stored copy if it's writable. Unreachable → note it in one line and skip (the one exemption from fail-fast).
1. **Inputs** — ask (tersely): which project, which environment, service name. List candidates via the host CLI first so the user picks instead of typing.
2. **Discover** — enumerate the environment's services (host file explains how). Identify: the shared DB server(s), Vault, Kubo, telemetry collector, and any existing agent-node (template). Resolve the Vault-token state now (host file) — a token refusal must fire before anything is created.
3. **Database** — pick driver (single DB server → use it; several → ask). Provision with the bundled script (needs only python3 — it installs its own DB driver into a temp venv, works on macOS/Linux/Windows):
   ```bash
   python3 scripts/provision_agent_db.py <postgres|mysql|mariadb> "<admin-public-url>"
   ```
   It creates database + user + grants and prints `DB_NAME`/`DB_USER`/`DB_PASSWORD`. Don't hand-roll SQL through psql/docker instead — the script encodes the sharp edges (postgres `CREATE DATABASE` refuses transactions; grants must run connected to the new database).
   **If the DB server is unreachable** (connect refused/timeout), the fail-fast contract applies: report which URL failed and stop — no alternate proxies, tunnels, `railway run`/SSH, docker, or hand-rolled SQL.
4. **Variables** — build the full variable set per the host file: host-reference templates for infra-dependent values, copies from the template node for the rest, fresh `JWT__SIGNING_KEY`, and the DB URL for the chosen driver. No template node → apply the question policy.
5. **Create & deploy** — create the service from repo `6022-labs/agentic-agent-node` with **all variables set at creation time** (a node booting without config just crash-loops), attach a public domain, and enable the host's sleep/serverless mode (see the host file) so an idle node stops billing.
6. **Verify** — poll until the deployment succeeds (build takes minutes), then `curl -s https://<domain>/` (expect 200 — the web console; don't use `/.well-known/6022`, it 404s `draft_not_found` by design until an agent is registered). On failure read the service logs; usual culprits: missing variable or bad DB URL (test the DB URL through the server's public proxy).
7. **Report** — the mandatory summary above.

## Cleanup on failure

Don't leave orphans: drop the just-created database and user (`DROP DATABASE`/`DROP ROLE` or `DROP USER`), or tell the user exactly what was created so they can decide.
