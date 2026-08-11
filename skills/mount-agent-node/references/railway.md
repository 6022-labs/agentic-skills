# Railway host strategy

Never assume a project: `railway list` → user picks (workflow step 1).

## Transport: MCP first, CLI fallback

Railway MCP tools present in session → prefer them (no binary, no login, structured output). Snippets below stay the canonical *what* + values — map operations, don't skip them. No MCP / operation missing → CLI exactly as written. Mixing transports per-operation = fine; retrying a failed operation on the other transport = never (failure means something's wrong: auth, project, service — diagnose, fail-fast per SKILL.md). Neither transport → failure summary; next action = connect Railway MCP or install + login CLI.

## Link

CLI is directory-scoped — link from a scratch dir so the user's repo stays unbound:

```bash
railway list
railway link -p <PROJECT_ID> -e <ENVIRONMENT>
railway environment list
```

## Discover services

```bash
railway status --json
```

- Agent-nodes: `latestDeployment.meta.repo == "6022-labs/agentic-agent-node"` **and** no `rootDirectory` (Grafana services build from the same repo under `/grafana/...` — shared, skip).
- DB servers: service named `Postgres` / `MySQL` / `MariaDB` (Railway templates).
- Capture environment `id` + each `serviceId` (dashboard links).

Variables dump (resolved values):

```bash
railway variables --service "<NAME>" --kv
```

## Database admin URL

Public proxy only — `railway.internal` unreachable from your machine. Postgres → `DATABASE_PUBLIC_URL`; MySQL/MariaDB → `MYSQL_PUBLIC_URL` (on the DB service).

## Variables

`${{...}}` references wherever a value depends on another service or self — references survive infra changes, hardcoded hostnames rot. Never set `RAILWAY_*` (auto-injected).

**Reference templates (exact values):**

| Variable | Value |
|---|---|
| `TELEMETRY__NODE_ID` | `${{RAILWAY_SERVICE_ID}}` |
| `AGENT_IDENTITY__RUNTIME_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` |
| `SIWE__URI` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` |
| `VAULT__ADDRESS` | `http://${{Vault.RAILWAY_PRIVATE_DOMAIN}}:${{Vault.PORT}}` |
| `IPFS__NODE_HTTP_URL` | `http://${{Kubo.RAILWAY_PRIVATE_DOMAIN}}:5001` |
| `TELEMETRY__ENDPOINT` | `${{Grafana Alloy.RAILWAY_PRIVATE_DOMAIN}}:4317` |

**Database (driver from workflow step 3; `<u>/<p>/<db>` from provision script):**

| Driver | `RUNTIME_DATABASE__DRIVER` | `RUNTIME_DATABASE__URL` |
|---|---|---|
| postgres | `postgres` | `host=${{Postgres.RAILWAY_PRIVATE_DOMAIN}} port=${{Postgres.PGPORT}} user=<u> password=<p> dbname=<db>` |
| mysql | `mysql` | `<u>:<p>@tcp(${{MySQL.RAILWAY_PRIVATE_DOMAIN}}:${{MySQL.MYSQLPORT}})/<db>?charset=utf8mb4&parseTime=True` |
| mariadb | `mariadb` | `<u>:<p>@tcp(${{MariaDB.RAILWAY_PRIVATE_DOMAIN}}:${{MARIADB_PORT-or-MYSQLPORT}})/<db>?charset=utf8mb4&parseTime=True` |

(Reference prefix = actual service name from discovery — `${{Postgres.…}}` only if the service is named `Postgres`.)

**Copy verbatim from template node:** everything else in its `--kv` dump except `RAILWAY_*` and `VAULT__TOKEN` (own rules below). Currently: `BLOCKCHAIN__EVM_CHAINS__*__HTTP_URL`, `PAYMENT__FACILITATORS__*__BASE_URL`, `INFERENCE_LOOP__MAX_ITERATIONS`, `JWT__NONCE_TTL`, `JWT__TOKEN_TTL`, `RUNTIME_CLIENT__TIMEOUT`, `TELEMETRY__ENABLED`, `TELEMETRY__INSECURE`, `VAULT__KV_V1_MOUNT_PATH`, `VITE_TX_GAS_MULTIPLIER` — trust the live dump, the surface evolves. No template → SKILL.md question policy.

## Vault token (`VAULT__TOKEN`)

Unlocks every agent's secrets ⇒ never set from a value you can read. Resolve state during discovery (step 2), before provisioning — a refusal must leave nothing behind. Two reads:

```bash
# 1. Environment shared variables (no serviceId → shared set)
railway api 'query { variables(projectId: "<PID>", environmentId: "<EID>") }'
# 2. Template node with references intact
railway api 'query { variables(projectId: "<PID>", environmentId: "<EID>", serviceId: "<TEMPLATE_SID>", unrendered: true) }'
```

(MCP `list-variables` if it can target the environment / return unrendered; else the passthrough above. Sealed values are never returned by the API — unreadability is the signal, not an error.)

First matching state wins; messages = templates below **verbatim** (fill placeholders, add nothing):

| State | Action |
|---|---|
| Shared set returns `VAULT__TOKEN` **with readable value** (shared, not sealed) | ⛔ Refuse with R1 |
| Template unrendered `VAULT__TOKEN` = `${{shared.VAULT__TOKEN}}`, nothing readable (sealed shared) | ✅ Sanctioned: set `VAULT__TOKEN=${{shared.VAULT__TOKEN}}` on the new node — value never seen by anyone, including you. Tell the user nothing. |
| Template `VAULT__TOKEN` = raw readable service-scoped value | ⛔ Refuse with R2 |
| Nothing readable, no shared reference (sealed service-scoped or fresh env) | Ask Q1 (batched with the other questions) |

**R1 — unsealed shared token:**

```
⛔ Can't mount: VAULT__TOKEN is a readable shared variable.
Fix: Project Settings → Shared Variables → ⋯ → Seal (dashboard-only, permanent), then re-run.
Or paste a token here — your responsibility.
```

**R2 — readable service-scoped token:**

```
⛔ Can't mount: VAULT__TOKEN is readable on <template-service>.
Fix: add VAULT__TOKEN as a shared variable, seal it, set <template-service>'s VAULT__TOKEN to ${{shared.VAULT__TOKEN}}, then re-run.
Or paste a token here — your responsibility.
```

**Q1 — no token found:**

```
Vault token? Paste it (your responsibility — seal it after), or reply "shared" if a sealed shared VAULT__TOKEN already exists.
```

User-pasted token = always accepted — their decision, their responsibility. Set as service variable, append **W1** to the final summary:

**W1 — seal warning after a user-passed token:**

```
⚠️ VAULT__TOKEN is readable on <service-name>.
Seal it: service → Variables → ⋯ → Seal (dashboard-only, permanent). Better: move it to a sealed shared variable.
```

## Create service + domain

```bash
railway add --service "<SERVICE_NAME>" --repo 6022-labs/agentic-agent-node \
  --variables "KEY1=value1" --variables "KEY2=value2" ...
railway service source connect --repo 6022-labs/agentic-agent-node --branch main --service "<SERVICE_NAME>"
railway domain --service "<SERVICE_NAME>" --port 5000
```

Repeat `--variables` per entry; single-quote values containing `${{...}}`. Repo builds from root Dockerfile — no build config. First build starts immediately ⇒ all variables at creation. Domain targets **port 5000** (the node's nginx listener).

**Branch pin — always `main`.** The connected branch is the autodeploy trigger: Railway auto-deploys every push to it by default. Pin it explicitly with the `source connect --branch main` line above (don't rely on the default-branch fallback). Autodeploy needs ≥1 project member's GitHub account with contributor access to the repo; if it ends up disabled, append one line to the summary: `⚠️ Autodeploy disabled — enable: service → Settings → GitHub trigger (check Railway GitHub App access).`

**Known CLI issue:** `railway add` can return "Unauthorized" despite a valid session (reads still work); re-login doesn't fix. Fallback: `railway api` `serviceCreate` mutation (variables in the input ⇒ first build fully configured), then the `source connect --branch main` command above (the `serviceConnect` mutation fails "ServiceInstance not found"; if `source connect` also fails, `serviceInstanceUpdate(serviceId, environmentId, input: { source: { repo } })` connects repo + default branch), then `railway domain`. Build auto-starts once the source lands.

## Serverless (app sleeping)

Always enable — sleeps after 10 min without **outbound** packets, wakes on first request. Enable right after creation, during the first build (after a deploy needs a redeploy):

- MCP: `update-service` with `sleepApplication: true`
- CLI: `railway api` mutation `serviceInstanceUpdate(serviceId, environmentId, input: { sleepApplication: true })`

Node's own outbound (OTEL, DB) may keep it awake — enable anyway. First request to a slept node may 502 (cold boot).

## Verify

```bash
railway status --json                     # poll latestDeployment.status → SUCCESS
railway logs --service "<SERVICE_NAME>"   # on failure / crash-loop
```

Idle-node 502 = serverless cold boot — retry once before diagnosing.

## Links for the summary

- Node: `https://<RAILWAY_PUBLIC_DOMAIN>` (from `railway domain` or the service's variables)
- Dashboard: `https://railway.com/project/<PROJECT_ID>/service/<SERVICE_ID>?environmentId=<ENVIRONMENT_ID>` (ids from `railway status --json`)
