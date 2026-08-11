# Railway host strategy

All Railway specifics for mounting an agent-node. Never assume a project: list them (`railway list`) and ask the user which one to use (workflow step 1).

## Transport: MCP first, CLI as fallback

Before running any command below, check whether a Railway MCP connector is available (search the session's tools for `railway`-named MCP tools — e.g. the official `@railway/mcp-server`). Prefer it when present: it needs no local binary or `railway login`, and its calls return structured data instead of text to parse.

- **MCP available** → use the MCP tool that performs the equivalent operation (list projects, read variables, create service, set domain, read logs). The snippets below stay the canonical definition of *what* each step does and *which* values it needs — map them, don't skip them.
- **No MCP tools, or a needed operation is missing from the connector** → use the CLI exactly as written below. Mixing transports mid-mount is fine per operation, but never retry a failed operation on the other transport to paper over an error — a failure means something is wrong (auth, wrong project, missing service); diagnose it, per the fail-fast rule in SKILL.md.
- **Neither transport reachable** → stop and report via the failure summary; the one next action is connecting the Railway MCP server or installing + logging into the CLI.

## Link

The CLI is directory-scoped — link from a scratch directory so the user's repo stays unbound:

```bash
railway list                                  # candidate projects
railway link -p <PROJECT_ID> -e <ENVIRONMENT>
railway environment list                      # candidate environments
```

## Discover services

```bash
railway status --json
```

- **Agent-nodes**: `latestDeployment.meta.repo == "6022-labs/agentic-agent-node"` **and no `rootDirectory`**. (The Grafana services build from the same repo under `/grafana/...` — shared, skip.)
- **DB servers**: service named `Postgres` / `MySQL` / `MariaDB` (Railway database templates).
- Capture `id` (environment), `serviceId` per service — needed for dashboard links.

Dump any service's variables (resolved values):

```bash
railway variables --service "<NAME>" --kv
```

## Database admin URL

External access goes through the public proxy (`railway.internal` is unreachable from your machine):

- Postgres: `DATABASE_PUBLIC_URL` on the Postgres service
- MySQL/MariaDB: `MYSQL_PUBLIC_URL` on the MySQL/MariaDB service

## Variables

Use Railway references wherever a value depends on another service or the service itself — references survive infra changes, hardcoded resolved hostnames rot. Never set `RAILWAY_*` variables (injected automatically).

**Reference templates (exact values):**

| Variable | Value |
|---|---|
| `TELEMETRY__NODE_ID` | `${{RAILWAY_SERVICE_ID}}` |
| `AGENT_IDENTITY__RUNTIME_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` |
| `SIWE__URI` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` |
| `VAULT__ADDRESS` | `http://${{Vault.RAILWAY_PRIVATE_DOMAIN}}:${{Vault.PORT}}` |
| `IPFS__NODE_HTTP_URL` | `http://${{Kubo.RAILWAY_PRIVATE_DOMAIN}}:5001` |
| `TELEMETRY__ENDPOINT` | `${{Grafana Alloy.RAILWAY_PRIVATE_DOMAIN}}:4317` |

**Database (driver chosen in workflow step 3; `<u>/<p>/<db>` from the provision script):**

| Driver | `RUNTIME_DATABASE__DRIVER` | `RUNTIME_DATABASE__URL` |
|---|---|---|
| postgres | `postgres` | `host=${{Postgres.RAILWAY_PRIVATE_DOMAIN}} port=${{Postgres.PGPORT}} user=<u> password=<p> dbname=<db>` |
| mysql | `mysql` | `<u>:<p>@tcp(${{MySQL.RAILWAY_PRIVATE_DOMAIN}}:${{MySQL.MYSQLPORT}})/<db>?charset=utf8mb4&parseTime=True` |
| mariadb | `mariadb` | `<u>:<p>@tcp(${{MariaDB.RAILWAY_PRIVATE_DOMAIN}}:${{MARIADB_PORT-or-MYSQLPORT}})/<db>?charset=utf8mb4&parseTime=True` |

(Reference prefix = the actual service name from discovery — `${{Postgres.…}}` only if the service is named `Postgres`.)

**Copied verbatim from the template agent-node:** everything else in its `--kv` dump that isn't `RAILWAY_*` or `VAULT__TOKEN` (own rules below). At the time of writing: `BLOCKCHAIN__EVM_CHAINS__*__HTTP_URL`, `PAYMENT__FACILITATORS__*__BASE_URL`, `INFERENCE_LOOP__MAX_ITERATIONS`, `JWT__NONCE_TTL`, `JWT__TOKEN_TTL`, `RUNTIME_CLIENT__TIMEOUT`, `TELEMETRY__ENABLED`, `TELEMETRY__INSECURE`, `VAULT__KV_V1_MOUNT_PATH`, `VITE_TX_GAS_MULTIPLIER` — but trust the live dump over this list; the config surface evolves. No template node → question policy from SKILL.md.

## Vault token (`VAULT__TOKEN`)

The Vault token unlocks every agent's secrets, so it is exempt from copy-verbatim: never set it from a value you can read. Resolve its state during discovery (workflow step 2), before provisioning anything — a refusal must leave nothing behind. Two reads settle it:

```bash
# 1. Environment shared variables (no serviceId → the shared set)
railway api 'query { variables(projectId: "<PID>", environmentId: "<EID>") }'
# 2. Template node with references intact (does it point at ${{shared.…}}?)
railway api 'query { variables(projectId: "<PID>", environmentId: "<EID>", serviceId: "<TEMPLATE_SID>", unrendered: true) }'
```

(MCP: `list-variables` if the connector can target the environment / return unrendered values; otherwise the CLI passthrough above. Sealed values are never returned by the API — unreadability is the signal, not an error.)

First matching state wins:

| State | Action |
|---|---|
| Shared set returns `VAULT__TOKEN` **with a readable value** — shared but not sealed | ⛔ Refuse the mount (failure summary). Next action: seal it — dashboard only, Project Settings → Shared Variables → ⋯ → Seal (permanent; no CLI/API can seal) — then re-run; or pass a token explicitly. |
| Template's unrendered `VAULT__TOKEN` is `${{shared.VAULT__TOKEN}}` and no readable value surfaced — sealed shared | ✅ The sanctioned state. Set `VAULT__TOKEN=${{shared.VAULT__TOKEN}}` on the new node; the value is never seen by anyone, including you. |
| Template's `VAULT__TOKEN` is a raw readable service-scoped value | ⛔ Refuse the mount. Next action: migrate it to a **sealed shared variable** (add as shared in the dashboard, seal it, swap the template's raw value for `${{shared.VAULT__TOKEN}}`), then re-run; or pass a token explicitly. |
| Nothing readable and no shared reference — sealed service-scoped token, or fresh environment | Ask the user: a raw token (explicitly theirs to give — their responsibility), or, if a sealed shared `VAULT__TOKEN` already exists that the API can't show, say so and wire the `${{shared.VAULT__TOKEN}}` reference. |

A token the user passes explicitly is always accepted — handing it over is their deliberate decision and their responsibility. Set it as a service variable on the new node, then append to the final summary:

```
⚠️ VAULT__TOKEN is readable on <service-name>. Seal it (service → Variables → ⋯ → Seal — dashboard-only, permanent), or better: move it to a sealed shared variable so future mounts wire ${{shared.VAULT__TOKEN}} without ever exposing it.
```

## Create service + domain

```bash
railway add --service "<SERVICE_NAME>" --repo 6022-labs/agentic-agent-node \
  --variables "KEY1=value1" --variables "KEY2=value2" ...
railway domain --service "<SERVICE_NAME>" --port 5000
```

Repeat `--variables` per entry; single-quote values containing `${{...}}` so the shell doesn't eat them. The repo builds from its root Dockerfile on branch `main` — no build config needed. The first build starts immediately, which is why all variables go in at creation. The domain must target **port 5000** (the node's nginx listener).

**Known CLI issue:** `railway add` can return "Unauthorized. Please run `railway login` again" even with a valid session (reads still work). Re-login does not fix it. Fall back to the GraphQL passthrough: create the service with `railway api` (`serviceCreate` mutation — variables can be included in the input so the first build is fully configured), then `railway domain`. If connecting the GitHub repo separately, note the `serviceConnect` mutation fails with "ServiceInstance not found" — use `serviceInstanceUpdate(serviceId, environmentId, input: { source: { repo } })` instead; the build auto-starts once the source lands.

## Serverless (app sleeping)

Every agent-node mounts with Serverless enabled — Railway sleeps the service after 10 min without **outbound** packets and wakes it on the first request. Enable it right after creating the service, while the first build runs (enabling after a deploy needs a redeploy):

- **MCP**: `update-service` with `sleepApplication: true`.
- **CLI**: no flag — `railway api` mutation `serviceInstanceUpdate(serviceId, environmentId, input: { sleepApplication: true })`.

The node's own outbound traffic (OTEL exports, DB connections) can keep it awake — enable anyway. First request to a slept node may 502 (cold boot).

## Verify

```bash
railway status --json          # poll new service's latestDeployment.status → SUCCESS
railway logs --service "<SERVICE_NAME>"   # on failure / crash-loop
```

A 502 from an idle node is the serverless cold boot — retry once before diagnosing.

## Links for the summary

- Node: `https://<RAILWAY_PUBLIC_DOMAIN>` (printed by `railway domain`, or in the service's variables)
- Dashboard: `https://railway.com/project/<PROJECT_ID>/service/<SERVICE_ID>?environmentId=<ENVIRONMENT_ID>` (ids from `railway status --json`)
