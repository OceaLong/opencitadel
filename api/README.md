# OpenCitadel API and Execution Kernel

[简体中文](README.zh-CN.md)

The Python backend has three explicit process roles. PostgreSQL execution
events are the only workflow authority; Redis is a disposable wake-up channel.

| Role             | Entrypoint                                          | Responsibility                                                                                                |
| ---------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| API              | `app.main` / `run.sh`                               | Authentication, authorization, command admission, projection queries, SSE                                     |
| Execution kernel | `app.execution_kernel_main` / `execution-kernel.sh` | Inbox, decisions, Activities, timers, outbox, projectors, scheduler, evaluation and comparison/export workers |
| Migrate          | `app.migrate` / `migrate.sh`                        | Greenfield Alembic schema and typed Runtime Policy seed                                                       |

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

![Backend module boundaries](../docs/assets/diagrams/backend-module-map.png)

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
- `/execution-runs/*`, `/execution-artifacts/*`, `/execution-sources/*`: workbench, pinned `at`, bounded Step/Timeline/Body pages, events and SSE
- `/execution-analysis/*`, `/execution-comparisons/*`: captured analysis, timezone preferences, comparison revisions, diff jobs and private CSV/JSON exports
- `/evaluation/*`: datasets, configuration/rubric/suite, preflight and batches, recorded/isolated environments, scores/reviews and protected archival
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

OpenAPI at `/openapi.json` is the route-level source of truth; A2A discovery also
has a root-level Well-known entry. SSE feed cursors use the public feed sequence,
not a formal event position. Workbench historical reads use a separate `PlaybackBoundary`.

Database suites require fresh schema roles and PostgreSQL/Redis. Ordinary tests
may skip integration cases when dependencies are absent; `make test-api-strict`
requires them, so skipped tests cannot establish integration success. See the
[deployment guide](../docs/operations/deployment.md) for role bootstrap.

CI and both Make targets exclude only `test_execution_visualization_closed_loop.py`.
Its six current-invocation consumers run through the [acceptance runner](../e2e/README.md)
after the native strict report and restoration receipt validate, with zero skips.

## Local development

```bash
uv sync --all-groups
uv run pytest -q --ignore=tests/app/integration/test_execution_visualization_closed_loop.py
uv run lint-imports
uv run ruff check --config ../ruff.toml . ../ops-actuator ../ops-collector ../sandbox ../scripts ../demo
```

Run the roles in separate terminals after configuring `.env` and PostgreSQL:

```bash
uv run ./migrate.sh
uv run ./run.sh
uv run ./execution-kernel.sh
```

Alembic has one linear lineage from `0001greenfield` to
`0030evaluation_judge_history`; a new database applies the complete `upgrade head`.
This is not a supported data-upgrade contract for older production releases.
There is no historical data conversion command or alternate execution schema.

## Containers

The Dockerfile exposes `api` and `execution-kernel` targets. Compose service
names are `opencitadel-api`, `opencitadel-execution-kernel`, and
`opencitadel-migrate`. The Helm chart uses the same API/kernel split and
dedicated credentials.

See [architecture overview](../docs/architecture/overview.md),
[execution kernel](../docs/architecture/execution-kernel.md), and
[deployment](../docs/operations/deployment.md).

- [Execution analysis, comparisons, and exports](../docs/architecture/execution-analysis.md)
- [Evaluation control plane](../docs/architecture/evaluation-control-plane.md)
