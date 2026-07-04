[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel API Service

FastAPI backend providing session management, AI Agent orchestration, model management, Skill templates, long-term memory, file handling, and sandbox management.

Agent tasks run in a **separate Worker process**; the API layer is stateless and supports horizontal scaling across replicas.

## Tech Stack

- Python 3.12+
- FastAPI + Uvicorn
- SQLAlchemy (asyncpg) + Alembic + **pgvector**
- Redis 7 (task dispatch consumer groups + Streams event pipeline)
- Docker SDK + SandboxProvider (sandbox pooling abstraction)
- Playwright (browser control in Worker; Chromium runs inside sandbox via CDP)
- OpenTelemetry + Prometheus (optional observability)
- MCP SDK / httpx (MCP, A2A, Anthropic, Gemini)

## Architecture Overview

API and Worker are separate processes sharing infrastructure and service providers from `BaseContainer`, each with a role-specific container:

| Role | Entry | Container | Docker target |
|------|-------|-----------|---------------|
| API | `app.main` | `ApiContainer` | `api` → `opencitadel-api` |
| Worker | `app.worker.main` | `WorkerContainer` | `worker` → `opencitadel-worker` |
| Migrate | `app.migrate` | — | `api` (one-off job) |

- **API**: HTTP/SSE, task dispatch, event stream reads, MCP/A2A connection pool recycling
- **Worker**: consumes `task:dispatch`, runs Agents, sandbox warm gateway and orphan cleanup
- **Migrate**: standalone job (`python -m app.migrate`); API startup only validates schema version

See [`../docs/architecture/overview.md`](../docs/architecture/overview.md) for full architecture details.

## Project Structure

```
api/
├── app/
│   ├── interfaces/            # FastAPI routes, schemas, middleware, auth DI
│   ├── application/
│   │   └── services/          # AgentService, TaskRunnerFactory, LLM*Service...
│   ├── domain/
│   │   ├── models/            # Domain entities (session, llm_endpoint, event...)
│   │   ├── repositories/      # UoW and repository ports
│   │   ├── external/          # External service ports
│   │   ├── services/agents/   # Planner, Clarify, ReAct, SubAgent
│   │   ├── services/flows/    # PlannerReActFlow, CodeAskFlow, DocQAFlow, HybridAskFlow
│   │   └── schemas/           # Structured LLM outputs
│   ├── infrastructure/
│   │   ├── repositories/      # DB repository implementations
│   │   ├── adapters/          # Redis, storage, event projection adapters
│   │   ├── external/task/     # RedisStreamTask, TaskStateService
│   │   ├── external/sandbox/  # DockerSandbox, SandboxProvider
│   │   ├── external/llm/      # OpenAI, ResilientLLM, circuit breaker
│   │   ├── observability/     # OTel, AgentTracer, logging_context
│   │   └── security/          # ApiKeyCipher, SecretManager
│   ├── runtime_role.py        # ProcessRole (api/worker/migrate)
│   ├── container.py           # BaseContainer / ApiContainer / WorkerContainer
│   ├── worker/main.py         # Agent Worker entry
│   ├── migrate.py             # Standalone migration entry
│   └── main.py                # FastAPI entry
├── alembic/
├── core/config.py
├── migrate.sh / worker.sh / run.sh
└── Dockerfile
```

## API Routes

All routes below are prefixed with `/api` unless noted. Authenticated routes require a valid session JWT unless using `X-Api-Key` on supported integration endpoints.

### Public / unauthenticated

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` | Cookie session auth |
| GET | `/auth/me` | Current user |
| GET | `/auth/oauth/{provider}/login`, `/auth/oauth/{provider}/callback` | OAuth (Google/GitHub) |
| GET | `/status` | Health check |
| GET | `/llm/status` | LLM provider availability summary |
| GET | `/metrics` | Prometheus metrics |
| GET | `/marketplace/apps` | Marketplace catalog |
| POST | `/marketplace/*` | Marketplace mini-app endpoints |
| POST | `/webhooks/{job_token}` | Automation webhook ingress |
| GET | `/share/artifact/{token}` | Public artifact share (no auth) |
| GET | `/.well-known/agent-card.json` | A2A agent card (when enabled) |
| POST | `/a2a` | Inbound A2A (feature-flagged) |

### Admin & compliance (auditor or admin)

| Method | Path | Description |
|--------|------|-------------|
| GET/PATCH/DELETE | `/admin/users`, `/admin/users/{id}` | User management |
| POST | `/admin/invitations` | Platform invitations |
| GET/PUT | `/admin/users/{id}/quota` | User quotas |
| GET | `/admin/audit`, `/admin/audit/{id}`, `/admin/audit/summary`, `/admin/audit/export` | Audit logs |
| GET | `/admin/usage`, `/admin/usage/summary`, `/admin/usage/timeseries`, `/admin/usage/breakdown` | Token usage APIs |
| GET | `/admin/overview` | Admin dashboard metrics |
| GET/DELETE/PATCH | `/admin/teams`, `/admin/teams/{id}` | Team administration |
| GET | `/admin/audit/verify-chain`, `/admin/audit/verify-chain/sessions/{id}` | Audit chain verification |
| GET | `/admin/evidence/sessions`, `/admin/evidence/sessions/{id}/package` | Compliance evidence |
| GET | `/admin/compliance/report` | Compliance report export |

### Teams & invitations

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/teams` | List/create teams |
| GET/DELETE | `/teams/{id}` | Team details/delete |
| GET/POST/DELETE/PATCH | `/teams/{id}/members`, `/teams/{id}/invitations` | Members and invitations |
| POST | `/teams/{id}/leave` | Leave team |
| POST | `/invitations/{token}/accept` | Accept invitation |

### App configuration & integrations

| Method | Path | Description |
|--------|------|-------------|
| GET | `/app-config/sections` | List AppConfig sections |
| GET/PUT/DELETE | `/app-config/sections/{section}` | Read/update/delete section |
| GET/POST | `/app-config/revisions`, `/app-config/revisions/{id}/rollback` | Config revision history |
| GET/PUT | `/app-config/agent` | Agent config shortcut |
| GET/POST/PUT/DELETE | `/app-config/mcp-servers`, `/app-config/mcp-servers/{name}/*` | MCP server CRUD |
| GET/POST/DELETE | `/app-config/a2a-servers`, `/app-config/a2a-servers/{id}/*` | A2A server CRUD |

### LLM endpoints & models

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/llm-endpoints` | List/create provider endpoints (stores encrypted API key) |
| GET/PUT/DELETE | `/llm-endpoints/{id}` | Endpoint CRUD |
| GET/POST | `/llm-models` | List/create models (references `endpoint_id`) |
| GET/PUT/DELETE | `/llm-models/{id}` | Model CRUD |
| POST | `/llm-models/{id}/set-default` | Set default model |
| POST | `/llm-models/{id}/probe-multimodal` | Probe vision capability |

See [`../docs/architecture/llm-endpoints-and-models.md`](../docs/architecture/llm-endpoints-and-models.md).

### Sessions, artifacts, skills, memory, files

| Method | Path | Description |
|--------|------|-------------|
| POST/GET/PATCH | `/sessions`, `/sessions/stream`, `/sessions/{id}` | Session CRUD and list |
| POST | `/sessions/{id}/chat` | SSE streaming chat |
| GET | `/sessions/{id}/events` | Paginated event replay |
| GET/POST | `/sessions/{id}/memory`, `/memory/compact`, `/memory/clear` | Session agent memory |
| GET/POST | `/sessions/{id}/checkpoints`, `/checkpoints/{id}/restore` | Checkpoints and rollback |
| WS | `/sessions/{id}/vnc` | VNC WebSocket proxy |
| GET/POST | `/sessions/{session_id}/artifacts`, `/artifacts/{id}`, `/artifacts/{id}/share` | Artifacts |
| GET/POST/PUT/DELETE | `/skills`, `/skills/recommend`, `/skills/import` | Skill templates |
| GET/POST/PUT/DELETE | `/memories` | Long-term memory |
| POST/GET | `/files`, `/files/{id}/download` | File upload/download |
| GET/POST/DELETE | `/service-keys` | Service API keys |

### Codebase, knowledge base, automation

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/codebases`, `/codebases/{id}/*` | Code import, ingest SSE, symbols, sessions |
| GET/POST | `/knowledge-bases`, `/knowledge-bases/{id}/*` | KB CRUD, documents, ingest, reindex |
| GET/POST/PATCH/DELETE | `/scheduled-jobs`, `/scheduled-jobs/{id}/*` | Cron/webhook jobs |
| GET/POST | `/notifications`, `/notifications/stream` | User notifications |

### SSE Event Types

All SSE `data` includes `event_id`, `created_at`, `schema_version`, `visibility`, `channel`, and `persist` metadata. Historical events replay via `GET /api/sessions/{id}/events?after=<seq>&limit=100`; live events push through `/chat` SSE.

| Event | Description |
|-------|-------------|
| `clarify` | Clarifying question from ClarifyAgent |
| `message` | Full user or assistant message |
| `message_delta` | Assistant text delta (merged by `stream_id`) |
| `reasoning_delta` | Reasoning delta (default: `include_debug=true` only) |
| `tool_args_delta` | Tool args JSON delta (default: `include_debug=true` only) |
| `assistant_notice` | User-facing assistant notice |
| `session_status` | Server-authoritative session status |
| `debug_item` | Internal debug item (`include_debug=true`) |
| `title` | Session title update |
| `plan` | Plan event (with steps list) |
| `step` | Step event (id/status/description) |
| `subagent` | Sub-agent delegation status |
| `tool` | Tool call event (name/function/args/content) |
| `artifact` | Artifact workbench update |
| `approval` | Plan/tool approval gate state |
| `wait` | Waiting for user input |
| `usage` | Token usage event |
| `done` | Stream end |
| `error` | Error event |

See [`../docs/architecture/events.md`](../docs/architecture/events.md) for design details.

## Agent Capabilities

- **Token streaming**: `LLM.stream_invoke()` pushes deltas to SSE
- **Parallel tools**: multiple `tool_calls` per turn; browser/shell auto-locked
- **Structured Planner output**: `PlannerPlanSchema` Pydantic strict validation
- **Vector memory**: pgvector hybrid recall when `memory.vector_enabled: true` in `api/config.yaml`
- **Multi-provider**: OpenAI-compatible / Anthropic / Gemini native adapters

## Local Development

### Prerequisites

```bash
pip install uv
uv sync --frozen
playwright install
```

### Configuration

See `.env.example` (bootstrap and secrets) and `api/config.yaml` (runtime behavior). Key `.env` settings:

```bash
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=opencitadel
POSTGRES_HOST=localhost
REDIS_HOST=localhost
REDIS_PORT=6379
API_KEY_SECRET=            # Required: strong random value in production
EMBEDDING_API_KEY=         # Required when vector memory is enabled
```

Sandbox address, vector memory, OTEL toggles in `api/config.yaml`:

```yaml
sandbox:
  address: null             # Leave empty to create sandbox containers dynamically
memory:
  vector_enabled: false
observability:
  otel_enabled: false
```

### Start Services

```bash
# 1. Database migration (required first)
./migrate.sh

# 2. Start API
./run.sh
# or: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Start Worker in another terminal (required for Agent tasks)
./worker.sh
```

Visit `http://localhost:8000/docs` for API documentation.

### Database Migrations

```bash
# Recommended: standalone migration script (Alembic + data migration / config seed)
./migrate.sh
# or
python -m app.migrate

# Development: generate new migration
alembic revision --autogenerate -m "description"
# Apply migrations via full entry point
./migrate.sh
```

> **Note**: API startup validates DB schema is at Alembic head; startup fails if not migrated (skipped in test env).

### LLM API Key Encryption Migration

Set a strong random `API_KEY_SECRET` in `.env` for production (`openssl rand -hex 32`). `llm_endpoints.api_key_encryption` indicates storage format:

| Value | Meaning |
|-------|---------|
| `legacy_plaintext` | Legacy plaintext (compatible read) |
| `fernet_v1` | Encrypted with `API_KEY_SECRET` |

`python -m app.migrate` (or Docker Compose `opencitadel-migrate`) auto-encrypts legacy plaintext after Alembic. Standalone fix: `python -m app.migrate_llm_api_keys`.

API and Worker reject weak secrets when `ENV=production`.

## Docker Deployment

Multi-stage `Dockerfile` produces separate images:

| target | Image | CMD |
|--------|-------|-----|
| `api` | `opencitadel-api` | `./run.sh` |
| `worker` | `opencitadel-worker` | `./worker.sh` |

Deploy via root `docker-compose.yml`:

| Service | Description |
|---------|-------------|
| `opencitadel-migrate` | One-off init job (api target), Alembic + LLM key migration |
| `opencitadel-api` | Stateless FastAPI API (`target: api`) |
| `opencitadel-worker` | Agent Worker pool (`target: worker`, scalable) |
| `opencitadel-postgres` | `pgvector/pgvector:pg16` |
| `opencitadel-redis` | Task queue and event streams |

```bash
docker compose up -d --build
docker compose logs -f opencitadel-worker
```

Build-time `pip install uv` and `uv sync` default to Aliyun PyPI (see build args in root `docker-compose.yml`). Override with `PIP_INDEX_URL`, `UV_INDEX_URL`, `UV_VERSION`, `UV_HTTP_TIMEOUT`.

## Kubernetes

Helm chart at [`../deploy/helm/opencitadel/`](../deploy/helm/opencitadel/), including API/Worker Deployments, HPA, and migrate initContainer.
