# Execution Kernel Greenfield Cutover Evidence

[简体中文](execution-kernel-cutover-evidence.zh-CN.md)

This record defines the reproducible evidence contract for the destructive,
single-runtime cutover. OpenCitadel targets new installations and provides no
database, API, event, deployment, or runtime compatibility with predecessor
builds.

## Final architecture boundary

- PostgreSQL Commands, Events, Inbox, Outbox, Activities, Timers, Snapshots, and
  formal projections are the only durable execution protocol.
- `api/app/execution_kernel_main.py` is the only execution runtime entrypoint.
- Redis is wake-up and notification infrastructure only. Losing Redis state does
  not lose accepted work or workflow state.
- Agent, Ask, knowledge ingestion, codebase indexing, automation, Patrol, and
  remediation use the same Run aggregate and command path.
- Approval is a formal command/state transition. No predecessor execution or
  human-intervention lifecycle remains in production code.

## Destructive schema contract

The repository has one Alembic head: `0001greenfield_initial`. It creates a new
schema and does not upgrade predecessor data. PostgreSQL provisioning installs
`vector`, `uuid-ossp`, and `pgcrypto`, then separates migration, application,
and execution-kernel login roles.

The strict database suite exercises a fresh PostgreSQL installation, forced RLS,
cross-tenant denial, stream-owner matching, immutable event/audit rows, hash
tamper detection, snapshot corruption fallback, and role grants. Patrol and
remediation records cannot bypass formal admission. Session turns are serialized
by a PostgreSQL row lock and keyed by an immutable request UUID.

## Verification authority

Current status comes from executable gates, never hand-maintained pass/skip
totals:

| Boundary | Authoritative evidence |
| --- | --- |
| Required product journeys | [`contracts/acceptance-evidence.schema.json`](../../contracts/acceptance-evidence.schema.json) and the zero-skip reporter |
| Full-stack result | `tmp/acceptance/<run-id>/manifest.json` locally; the `acceptance-evidence` artifact from the required [`acceptance-e2e` CI job](../../.github/workflows/ci.yml) |
| API, database, RLS, architecture | `OPENCITADEL_REQUIRE_POSTGRES_TESTS=1 uv run pytest -q` and `uv run lint-imports` |
| UI | format, i18n, generated API contract, typecheck, lint, unit suite, and production build in CI |
| Deployment/release | Compose render, Helm lint/template, Kustomize renders, release matrix contracts, and image builds |
| Documentation | `./scripts/check-docs.sh` |

The acceptance manifest is content-hashed and includes requirement coverage,
production/acceptance image digests, Alembic head, service health and restart
state, Sandbox lifecycle, artifacts, and owned-resource residue. A full run is
successful only when every required ID is represented by a passing Playwright
test, no required test is skipped, the manifest validates, and cleanup reaches
the declared residue contract.

The acceptance inference provider is a test fixture under the Compose
`acceptance` profile. Release contracts prohibit it from Helm, Kustomize,
quickstart, production configuration, and the seven-image release matrix.
External-provider canaries are optional compatibility signals and are not
cutover evidence.

## Reproduce

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

The runner creates a unique Compose project and run namespace, uses only public
product setup paths, captures evidence on success and failure, and removes only
resources whose exact project/run labels match. See the
[E2E acceptance guide](../../e2e/README.md) for retained-volume behavior and
cleanup diagnostics.

## Negative residue proof

Repository contracts reject predecessor class names, module paths, tables,
deployment workloads, and environment settings. Formal `ActivityWorker`,
`DecisionWorker`, and `InboxWorker` classes are components of the single
execution kernel, not separate runtime lifecycles.

The cutover workflow does not stage or commit files.
