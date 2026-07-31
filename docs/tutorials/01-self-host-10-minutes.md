[简体中文](01-self-host-10-minutes.zh-CN.md)

# Tutorial 1: Self-Host OpenCitadel in 10 Minutes

This guide gets you from zero to your first Agent task using **BYO API Key** (OpenAI, Anthropic, or any OpenAI-compatible provider).

## Prerequisites

- Docker Desktop or Docker Engine + Compose v2
- 8 GB RAM minimum (16 GB recommended)
- An LLM API key from your provider

## Steps

### 1. Clone and configure

```bash
git clone https://github.com/OceaLong/opencitadel.git
cd opencitadel
make quickstart
```

The script copies `.env.example` → `.env`, generates secrets, and prompts you to set `BOOTSTRAP_ADMIN_PASSWORD`.

> **Local evaluation only:** quickstart deliberately sets
> `ENV=development`, `COOKIE_SECURE=false`, bundled MinIO, and localhost URLs.
> Do not expose or promote that `.env` to a public or multi-user deployment.
> Before production, follow the complete secret, database-role, Redis,
> trusted-proxy, egress, and verification procedure in the [production
> deployment guide](../operations/deployment.md).

### 2. Start the stack

`make quickstart` runs:

1. `docker compose build opencitadel-sandbox` — required for dynamic sandbox creation (the compose service is under the `fixed-sandbox` profile and is not started by default, but Worker-created sandboxes need this image)
2. `docker compose up -d --build` — starts API, Worker, UI, Postgres, Redis, and (with quickstart defaults) MinIO

First build may take 5–10 minutes.

Open **http://localhost:8088** when health check passes.

> **Storage default**: quickstart uses local MinIO storage out of the box. For cloud COS and other storage config, see [deployment guide — deployment mode](../operations/deployment.md#deployment-mode-env).

### 3. Log in

- Email: value of `BOOTSTRAP_ADMIN_EMAIL` (default `admin@example.com`)
- Password: your `BOOTSTRAP_ADMIN_PASSWORD`

### 4. Add an endpoint and model

LLM configuration is two-step: **endpoint** (provider + API key) then **model** (model name under that endpoint). Full details: [Production deployment — Models](../operations/deployment.md#models-skills-and-memory).

1. Open **Settings → Models**
2. Click **Add endpoint** — choose provider, base URL, paste API key
3. Under that endpoint, click **Add model** — enter model name
4. Set as default

### 5. Run your first task

From the home page, try:

> Summarize the top 3 trends in enterprise AI agents in 2026 and save a brief report as report.md

Watch the Agent plan, use tools in the sandbox, and stream results in real time.

## Fully offline (optional)

For air-gapped or local-only deployments, set in `.env`:

```bash
COMPOSE_PROFILES=local
STORAGE_PROVIDER=minio
COOKIE_SECURE=false
FRONTEND_BASE_URL=http://localhost:8088
OUTBOUND_PRIVATE_HOST_ALLOWLIST=host.docker.internal
```

Install [Ollama](https://ollama.com), pull a model, then add an **endpoint** (`http://host.docker.internal:11434/v1`) and a **model** under it in Settings. Keep the allowlist exact — no wildcard. Full local-mode reference: [deployment guide — local mode](../operations/deployment.md#local-mode).

**Note:** Smaller local models may struggle with multi-step Agent tasks. BYO cloud API keys give the best first-run experience.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| 502 on login | Wait for `opencitadel-migrate` to finish; check `docker compose logs opencitadel-migrate` |
| Agent does nothing | Confirm a default model is set and its endpoint has a valid API key |
| OOM / slow | See [deployment guide](../operations/deployment.md) memory tuning; enable swap on small VMs |

## Next

- [Tutorial 2: Internal knowledge base](./02-internal-knowledge-base.md)
- [Deployment guide](../operations/deployment.md)
