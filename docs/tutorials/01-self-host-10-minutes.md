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

### 2. Start the stack

`make quickstart` runs `docker compose up -d --build`. First build may take 5–10 minutes.

Open **http://localhost:8088** when health check passes.

### 3. Log in

- Email: value of `BOOTSTRAP_ADMIN_EMAIL` (default `admin@example.com`)
- Password: your `BOOTSTRAP_ADMIN_PASSWORD`

### 4. Add a model

1. Open **Settings → Models**
2. Click **Add model**
3. Choose provider (e.g. OpenAI), paste API key, select model name
4. Set as default

### 5. Run your first task

From the home page, try:

> Summarize the top 3 trends in enterprise AI agents in 2026 and save a brief report as report.md

Watch the Agent plan, use tools in the sandbox, and stream results in real time.

## Fully offline (optional)

For air-gapped or local-only deployments:

```bash
# In .env
COMPOSE_PROFILES=local
STORAGE_PROVIDER=minio
COOKIE_SECURE=false
FRONTEND_BASE_URL=http://localhost:8088
```

Install [Ollama](https://ollama.com) on the host, pull a capable model (e.g. `qwen2.5:14b`), then add it in Settings with base URL `http://host.docker.internal:11434/v1`.

**Note:** Smaller local models may struggle with multi-step Agent tasks. BYO cloud API keys give the best first-run experience.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| 502 on login | Wait for `opencitadel-migrate` to finish; check `docker compose logs migrate` |
| Agent does nothing | Confirm a default model is set with valid API key |
| OOM / slow | See [deployment guide](../operations/deployment.md) memory tuning; enable swap on small VMs |

## Next

- [Tutorial 2: Internal knowledge base](./02-internal-knowledge-base.md)
- [Deployment guide](../operations/deployment.md)
