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
│   ├── application/
│   │   ├── services/          # AgentService, TaskRunnerFactory, MemoryService...
│   │   └── ...
│   ├── domain/
│   │   ├── services/agents/   # BaseAgent, Planner, ReAct
│   │   ├── services/flows/    # PlannerReActFlow
│   │   └── schemas/           # PlannerPlanSchema and structured outputs
│   ├── infrastructure/
│   │   ├── external/task/     # RedisStreamTask, TaskStateService
│   │   ├── external/sandbox/  # DockerSandbox, SandboxProvider
│   │   ├── external/llm/      # OpenAI, Anthropic, Gemini
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

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Health check |
| GET | `/api/metrics` | Prometheus metrics |
| GET/POST | `/api/app-config` | App configuration |
| GET/POST | `/api/llm-models` | List and create models |
| GET/PUT/DELETE | `/api/llm-models/{id}` | Model details, update, delete |
| POST | `/api/llm-models/{id}/set-default` | Set default model |
| GET/POST | `/api/skills` | Skill template list and create |
| GET/PUT/DELETE | `/api/skills/{id}` | Skill template details, update, delete |
| GET/POST | `/api/memories` | Long-term memory list and create |
| GET/PUT/DELETE | `/api/memories/{id}` | Memory details, update, delete |
| POST | `/api/files` | File upload |
| GET | `/api/files/{id}/download` | File download |
| POST | `/api/sessions` | Create session |
| POST | `/api/sessions/stream` | SSE streaming session list |
| GET | `/api/sessions/{id}` | Session details |
| GET | `/api/sessions/{id}/events` | Paginated session events by cursor |
| PATCH | `/api/sessions/{id}` | Update session model or Skill config |
| POST | `/api/sessions/{id}/chat` | SSE streaming chat (with Token delta events) |
| GET | `/api/sessions/{id}/memory` | Session Agent memory |
| POST | `/api/sessions/{id}/memory/compact` | Compact session Agent memory |
| POST | `/api/sessions/{id}/memory/clear` | Clear session Agent memory |
| DELETE | `/api/sessions/{id}/memory/{agent_name}/messages/{index}` | Delete a memory message |
| WS | `/api/sessions/{id}/vnc` | VNC WebSocket proxy |
| GET/POST | `/api/codebases` | Code knowledge base import and management |
| GET | `/api/codebases/{id}/ingest` | Codebase ingest progress SSE |
| GET/POST | `/api/knowledge-bases` | Document knowledge base management |
| GET/POST | `/api/knowledge-bases/{id}/documents` | Document upload and ingest |

### SSE Event Types

All SSE `data` includes `event_id`, `created_at`, `schema_version`, `visibility`, `channel`, and `persist` metadata. Historical events replay via `GET /api/sessions/{id}/events?after=<seq>&limit=100`; live events push through `/chat` SSE.

| Event | Description |
|-------|-------------|
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
| `tool` | Tool call event (name/function/args/content) |
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
