# Governed Web Operator (end-to-end runbook)

This tutorial walks through the **governed enterprise Web Operator** scenario using the bundled **OpsConsole** demo backend.

## Prerequisites

- Docker Compose
- LLM API key configured in OpenCitadel Settings
- Web Operator skill enabled

## 1. Start platform + demo backend

```bash
cp .env.example .env
# set BOOTSTRAP_ADMIN_PASSWORD and LLM keys

docker compose --profile local --profile demo up --build
```

- OpenCitadel UI: http://localhost:8088
- OpsConsole (ticket ops backend): http://localhost:9099 (Docker network: `ops-console:9099`)

Demo login: `agent` / `agent123`

## 2. Create a Web Operator session

1. Open home, select **Web Operator** skill.
2. Send a task such as: *Log into OpsConsole, open ticket #2, change status to in_progress, then process refund with confirmation.*
3. In the **ownership dialog**, choose:
   - **Enterprise-owned**
   - Domain allowlist: `ops-console` (or `localhost` when testing from host browser)
   - Gate profile: **Standard** (plan + first-visit domain + critical actions)

## 3. Observe governance

| Step | Expected gate |
|------|----------------|
| Plan | One-time plan approval |
| First navigation to OpsConsole | Domain approval (unless in allowlist) |
| Update status / assign | No per-call gate (standard profile) |
| Refund / close | Per-call tool approval |
| Stuck automation | VNC takeover |
| Mistake | Checkpoint restore (includes browser profile on Docker) |
| Takeover timeout (30m) | Session paused → **Awaiting human** |

## 4. Audit artifacts

When the session completes, download:

- `audit-report.md` — human-readable summary
- `audit-report.json` — structured export with governance actions + tool invocations (redacted)

Also review `/admin/audit`.

## 5. Schedule as automation

1. Open **Automation** → create job with Web Operator skill.
2. Set `operator_scope`, domains, gate profile, optional MCP notify channel.
3. Use interval/cron/webhook trigger.

## 6. E2E tests

```bash
cd e2e && npm install && npx playwright test
```

Set `OPS_CONSOLE_URL=http://localhost:9099` when demo profile is running.

See also: [Web Operator architecture](../architecture/web-operator.md)
