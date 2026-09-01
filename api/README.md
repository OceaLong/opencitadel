# OpenCitadel API and Execution Kernel

[简体中文](README.zh-CN.md)

The Python backend has three explicit process roles. PostgreSQL execution
events are the only workflow authority; Redis is a disposable wake-up channel.

| Role | Entrypoint | Responsibility |
| --- | --- | --- |
| API | `app.main` / `run.sh` | Authentication, authorization, command admission, projection queries, SSE |
| Execution kernel | `app.execution_kernel_main` / `execution-kernel.sh` | Inbox, decisions, Activities, timers, outbox, projectors, scheduler |
| Migrate | `app.migrate` / `migrate.sh` | Greenfield Alembic schema and typed Runtime Policy seed |

The API never runs Agent or ingestion workflow steps. The execution kernel
polls durable PostgreSQL work and may also wait on Redis hints. Deleting Redis
cannot delete an accepted command, Activity, timer, event, or outcome.

## Technology

- Python 3.12, FastAPI, Pydantic 2
- SQLAlchemy 2 async, Alembic, PostgreSQL 16, pgvector
- Redis 7 for wake-up hints and cache only
- OpenAI, Anthropic, and Gemini model adapters
- MCP, A2A, Playwright, Docker/Kubernetes sandboxes
- OpenTelemetry and Prometheus

## Source map

```text
app/
├── domain/execution/           typed commands, events, aggregates, policies
├── application/execution/      orchestration, decisions, Activities, projectors
├── infrastructure/execution/   PostgreSQL stores and Redis wake-up adapter
├── composition/                manual typed API/kernel graphs and task ownership
├── interfaces/                 FastAPI routes, schemas, auth dependencies
├── application/services/       product application services
├── domain/                     product entities and ports
├── infrastructure/             repositories, providers, security, observability
├── execution_kernel.py         application-only kernel orchestration
├── execution_kernel_main.py
├── migrate.py
└── main.py
alembic/versions/0001greenfield_initial.py
```

All nondeterministic provider work is an Activity. An invocation identity,
input digest, timeout, policy snapshot, and call-start state are committed
before the external call. Completion returns through a typed command. Formal
Run, Activity, approval, resource-build, and public-event tables are
rebuildable projections, not alternate state machines.

## Composition and transactions

`app.main:create_app --factory` loads deployment settings once and installs a
lifespan-owned `ApiRuntime` on `app.state`. `app.execution_kernel_main` builds a
separate `KernelRuntime`. `TaskSupervisor` owns every background coroutine and
performs bounded drain; the roles do not share resource instances.

Application mutations call `uow.commit()` explicitly. Context exit without a
commit always rolls back, including a normal return. Repository methods never
commit, and Redis publication is a post-commit hint after PostgreSQL succeeds.

Use `/api/health/live` for process liveness and `/api/health/ready` for complete
runtime readiness. `/api/status` is a dependency diagnostic, not a lifecycle
probe.

## Security boundaries

Authenticated requests resolve an immutable `AuthorizationContext` and
`OwnerScope`. Transaction-local PostgreSQL settings drive forced RLS. The
greenfield deployment provisions separate application, execution-kernel, and
migration roles; schema ownership is not granted to runtime roles.

- User resources are personal or belong to one team workspace.
- Auditors are read-only.
- Administrators manage global resources and platform configuration.
- Cross-scope lookups fail closed and normally return not found.
- LLM and integration secrets use only versioned `fernet_v2` envelopes.

## Core HTTP contract

All application routes are under `/api`.

- `/auth/*`, `/teams/*`, `/service-keys/*`: identity and workspaces
- `/sessions/*`: session CRUD, message command admission, public event replay,
  VNC and files; `?q=` title/message search, and the soft-delete recycle bin
  (`GET /sessions/deleted`, `POST /sessions/{id}/delete|restore|purge`)
- `/runs/*`, `/approval-batches/*`: formal execution and approval commands
- `/approvals`: reviewer inbox — `GET /approvals?status=pending` (also
  `approved`/`rejected`/`cancelled`/`expired`) across Runs
- `/knowledge-bases/*`: immutable candidate builds and published version
  bindings, plus the soft-delete recycle bin (`GET /knowledge-bases/deleted`,
  `DELETE /knowledge-bases/{id}`, `POST /{id}/restore`,
  `DELETE /{id}/purge`)
- `/scheduled-jobs/*`, `/patrol-*`: automation, patrol, evidence, remediation;
  `GET /scheduled-jobs/{id}/runs` returns paginated firing history
- `/artifacts/*`: workspace artifacts with desensitized share fields
  (`is_shared`, `share_expires_at`, `share_token_preview`); the full share token
  is returned only once on create/rotate
- `/a2a` (inbound, `X-Api-Key`): A2A JSON-RPC — `message/send`,
  `message/stream`, `tasks/get`, `tasks/cancel`
- `/capabilities`: platform capability report including `report_pdf`
- `/inference/endpoints/*`, `/inference/models/*`, `/inference/bindings/*`,
  `/skills/*`, `/runtime-policies/*`: runtime resources, policy revisions, and inference bindings
- `/admin/*`: users, usage, audit, governance, compliance; team deletion
  (`cascade` | `transfer_to_owner`) and user deletion
  (`anonymize` | `cascade` | `transfer_to_team`) are explicit audited strategies

OpenAPI at `/openapi.json` is the route-level source of truth.

## Local development

```bash
uv sync
uv run pytest -q
uv run lint-imports
uv run ruff check --select F821 app tests
```

Run the roles in separate terminals after configuring `.env` and PostgreSQL:

```bash
./migrate.sh
./run.sh
./execution-kernel.sh
```

The migration is a single greenfield revision. There is no historical data
conversion command or alternate execution schema.

## Containers

The Dockerfile exposes `api` and `execution-kernel` targets. Compose service
names are `opencitadel-api`, `opencitadel-execution-kernel`, and
`opencitadel-migrate`. The Helm chart uses the same API/kernel split and
dedicated credentials.

See [architecture overview](../docs/architecture/overview.md),
[execution kernel](../docs/architecture/execution-kernel.md), and
[deployment](../docs/operations/deployment.md).
