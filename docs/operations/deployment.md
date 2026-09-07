# Deployment

[简体中文](deployment.zh-CN.md)

OpenCitadel deploys one stateless API and one database-authoritative execution
kernel. PostgreSQL is required; Redis only lowers wake-up latency. The shipped
schema is greenfield and has one Alembic revision, so deploy into a new
database rather than importing an earlier development catalog.

## Processes

| Process | Compose service | Database credential |
| --- | --- | --- |
| Migration | `opencitadel-migrate` | `POSTGRES_MIGRATION_*` |
| API | `opencitadel-api` | `POSTGRES_USER` / `POSTGRES_PASSWORD` |
| Execution kernel | `opencitadel-execution-kernel` | `POSTGRES_KERNEL_*` |
| UI | `opencitadel-ui` | none |

The PostgreSQL administrator credential is bootstrap-only. Runtime containers
must not receive it. The kernel runs command inbox, Run decisions, Activities,
timers, outbox delivery, formal projectors, automation, and maintenance ticks.
There is no second execution service.

Each role loads deployment settings once and constructs only its own manual
typed graph: the API owns `ApiRuntime`, while the execution process owns
`KernelRuntime`. Their `TaskSupervisor` instances and PostgreSQL, Redis,
storage, provider, and connection-pool resources are never shared.

## Compose quick start

```bash
cp .env.example .env
# Replace every required secret and password in .env.
docker compose --profile local up -d --build
docker compose ps
```

Open `http://localhost:8088`. The `local` profile enables bundled MinIO. Cloud
deployments can use COS by setting `STORAGE_PROVIDER=cos` and the `COS_*`
values.

At minimum, set strong distinct values for:

- `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`,
  `POSTGRES_MIGRATION_USER`, `POSTGRES_MIGRATION_PASSWORD`,
  `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_KERNEL_USER`,
  `POSTGRES_KERNEL_PASSWORD`
- `REDIS_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`
- `API_KEY_SECRET_ID`, `API_KEY_SECRET`, `API_KEY_PREVIOUS_SECRETS`
- `AUDIT_SIGNING_KEY_ID`, `AUDIT_SIGNING_KEY`, `AUDIT_PREVIOUS_SIGNING_KEYS`
- `JWT_SECRET`, `SESSION_SECRET`
- `SANDBOX_BROKER_TOKEN`, `SANDBOX_TOKEN_SEED`
- `OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS`

`SANDBOX_TOKEN_SEED` is required and must be at least 32 random bytes in
production; the API and execution kernel both derive each sandbox's data-plane
token from it. `JWT_PREVIOUS_SECRETS` (default `{}`) and
`DATABASE_AUTHORIZATION_SIGNING_SECRET` (default: reuse `SESSION_SECRET`) are
optional and covered under *Configuration and secrets*. When you run the Ops
Patrol Collector/Actuator, also set strong `OPS_COLLECTOR_TOKEN` and
`OPS_ACTUATOR_TOKEN` values; each server refuses to start without one.

Use at least 32 random bytes for cryptographic keys. Keep `COOKIE_SECURE=true`
outside local HTTP development. Set `FRONTEND_BASE_URL` and
`OAUTH_REDIRECT_BASE` to the public HTTPS origin, configure
`TRUSTED_PROXY_CIDRS` precisely, and restrict `OUTBOUND_ALLOWED_PORTS` and
`OUTBOUND_PRIVATE_HOST_ALLOWLIST`. In production `TRUSTED_PROXY_CIDRS` is
validated at startup and rejects broad RFC1918 ranges that overlap the
sandbox/pod network.

## Startup and recovery

Compose starts PostgreSQL/Redis, runs the one-shot migration, then starts API,
execution kernel, UI, and proxy. The API refuses to run against a schema that
is not at Alembic head.

```bash
docker compose logs -f opencitadel-migrate
docker compose logs -f opencitadel-api
docker compose logs -f opencitadel-execution-kernel
```

Restarting or scaling the kernel is safe: claims use database fencing and
pending work is reclaimed from PostgreSQL. Redis may be flushed or restarted;
the kernel polls pending rows when no hint arrives. Never treat Redis keys as
backup data.

## Health probes and bounded drain

The API exposes separate unauthenticated process probes:

- `/api/health/live` returns success while the HTTP process can serve;
- `/api/health/ready` returns success only after the complete `ApiRuntime` is
  constructed and turns unavailable before owned work is drained.

`/api/status` remains a dependency diagnostic and is not a Kubernetes
liveness probe. The kernel uses `python -m app.execution_kernel_health
readiness|liveness`; its owned heartbeat writes an atomic marker and removes it
on shutdown. Readiness also verifies Runtime Policy, schema, and the dedicated
kernel database role.

Set `OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS=30` for the bounded application drain.
Compose uses `stop_grace_period: 45s`; Helm uses `shutdown.timeoutSeconds: 30`
and `shutdown.terminationGracePeriodSeconds: 45`. The platform termination
grace must always be greater than the application timeout.

## Storage and sandbox

Production object storage must be shared by every API and kernel replica. Use
COS or S3-compatible MinIO with a private bucket; local filesystem storage is
not a multi-replica option.

Compose isolates Docker access in `opencitadel-sandbox-broker`. API and kernel
receive only its narrow token-authenticated HTTP endpoint, never the Docker
socket. On native Linux set `DOCKER_SOCK_GID` to the socket group. Kubernetes
uses a dedicated execution-kernel ServiceAccount and restricted sandbox Pod
RBAC. Keep the Squid sandbox egress proxy and allowlist enabled. Each sandbox's
data-plane token is derived as `HMAC(SANDBOX_TOKEN_SEED, sandbox_id)` on both the
API and kernel side; the seed never enters the sandbox container, so any replica
can re-attach and authenticate without shared token state.

## Configuration and secrets

Migration seeds typed Execution and Operations Policy revisions plus their
atomic head in an empty database. Administrators manage later immutable
revisions through **Settings → Runtime policy** or `/api/runtime-policies`.
Environment variables are reserved for deployment topology, identity,
credentials, endpoints, and bootstrapping; they never override policy fields.

Inference endpoint and integration credentials are stored only as versioned `fernet_v2`
envelopes. For key rotation:

1. Put the old key under its id in `API_KEY_PREVIOUS_SECRETS`.
2. Set a new `API_KEY_SECRET_ID` and `API_KEY_SECRET`.
3. Restart API and execution-kernel replicas.
4. Rotate provider credentials and save each affected endpoint/integration;
   new writes use the active key.
5. Remove the old key only after no stored envelope uses its id.

Rotate audit signing keys similarly with `AUDIT_PREVIOUS_SIGNING_KEYS`, and
session JWTs with `JWT_PREVIOUS_SECRETS`: move the old key under its id into the
previous map, set the new `JWT_SECRET`, and restart replicas; tokens still in
flight keep validating until they expire. `DATABASE_AUTHORIZATION_SIGNING_SECRET`
defaults to `SESSION_SECRET`, which keeps existing deployments and their seeded
RLS `app.rls_signing_secret` value unchanged; set it to a distinct strong value
only to split the DB authorization trust domain from the session cookie one. The
database stamps its copy of the secret during the first migration, so setting or
changing the value on an existing database requires running
`python -m app.rotate_db_signing_secret` (re-entrant; updates the stored secret
and verifies a signed probe in one transaction) and then restarting the api and
execution-kernel replicas. Never log plaintext secrets or copy them into Runtime
Policy.

After bootstrap, configure endpoint, typed model, and purpose bindings through
**Settings → Inference** or `/api/inference`. Chat, embedding, and rerank
consumers have no environment-key fallback; an unresolved binding is reported
through `/api/capabilities` and fails closed. The optional `DEMO_INFERENCE_*`
variables are only inputs to the explicit demo seed command.

## Observability

Set `METRICS_TOKEN` to expose authenticated API metrics. Set
`EXECUTION_KERNEL_METRICS_PORT` (default `9108`) for the internal kernel
Prometheus endpoint, and restrict it with network policy. Monitor:

- pending and oldest command, Activity, timer, and outbox age;
- Activity claim expiry, unknown outcomes, retries, and approval waits;
- projector lag and hash/integrity failures;
- PostgreSQL connections, storage, locks, and forced-RLS errors;
- sandbox quota, provider latency, and object-storage failures.

An integrity or OwnerScope error is fail-closed and requires investigation;
do not bypass it by modifying event rows.

## Helm

The chart is under `deploy/helm/opencitadel`.

```bash
helm lint deploy/helm/opencitadel
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml
```

Supply all secrets through a secret manager or protected values file. Keep
`networkPolicy.enabled=true`, separate API/kernel/migration database users,
and configure `executionKernel.replicas` plus its HPA for Activity volume.
Optional Ops Collector and Actuator workloads must remain network-separated;
the Actuator is reachable only from API/kernel and still requires a persisted
approval. Their RBAC is a namespaced `Role`/`RoleBinding` rendered per allowed
namespace, not a cluster-wide `ClusterRole`.

The chart ships the resilience and observability baseline as templates:
per-workload `NetworkPolicy` objects (PostgreSQL, Redis, execution kernel, Ops
Collector/Actuator, egress proxy, sandbox), PodDisruptionBudgets for the API and
execution kernel, a Squid `egress-proxy` Deployment that confines sandbox
outbound traffic to an allowlist, a PostgreSQL backup `CronJob`, and a
`PrometheusRule` with alerts for approval timeouts, audit-chain verification
failure, outbox lag, sandbox admission rejection, 5xx rate, and rate-limit
rejections. The reverse proxy also sets HSTS/CSP/nosniff response headers and
`server_tokens off`.

For chart-managed PostgreSQL, `files/postgres/init-app-role.sh` creates the
distinct migration, API, and kernel roles before the greenfield migration.
For an external database, provision equivalent roles before install. Verify
that runtime roles return `rolsuper=false` and `rolbypassrls=false`; never give
schema ownership or migration credentials to API or kernel containers.

## Release artifacts and supply chain

Release tags publish seven images: `api`, `execution-kernel`, `migrate`, `ui`,
`sandbox`, `ops-collector`, and `ops-actuator`. The
`.github/workflows/security.yml` gate runs Gitleaks, CodeQL, and Trivy. The
release workflow scans every image before publish and attaches an SBOM plus
signed provenance. Deploy by immutable digest after verifying provenance,
rather than relying on `latest`.

The deterministic inference provider under `e2e/fixtures/` is not a release
artifact. It exists only in the Compose `acceptance` profile and must never be
added to Helm, Kustomize, quickstart, production settings, or the release image
matrix.

## Deterministic acceptance gate

Run the same release-blocking full-stack gate used by CI:

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

The runner owns a unique Compose project and run namespace, configures inference
through the public control plane, exercises the real execution kernel and
Collector, and writes `tmp/acceptance/<run-id>/manifest.json`. The evidence
schema is `contracts/acceptance-evidence.schema.json`; missing, duplicated,
skipped, interrupted, or failed required IDs fail the gate.

Cleanup is label-bound to `com.docker.compose.project`,
`com.opencitadel.acceptance.project`, and `com.opencitadel.acceptance.run`.
Dynamic Sandboxes also require `opencitadel.io/sandbox=true` and the run-scoped
name prefix. With `--disposable`, invocation-owned volumes must reach zero.
Without it, volumes and product history are retained and reported, while
containers, networks, and dynamic Sandboxes are still drained.

CI publishes `tmp/acceptance/` as the `acceptance-evidence` artifact even when
the gate fails. Inspect the manifest `failure_reason`, `logs/stack.log`, and
Playwright traces/screenshots before retrying. Do not replace runner cleanup
with a broad Docker prune.

## Release gates

Before rollout, run:

```bash
cd api
uv run pytest -q
uv run lint-imports
uv run ruff check --select F821 app tests

cd ../ui
npm run i18n:check
npm run typecheck
npm run lint
npm run test
npm run build

cd ..
docker compose config
helm lint deploy/helm/opencitadel
./scripts/run-acceptance-e2e.sh --disposable
```

Database-backed execution/RLS tests require a disposable PostgreSQL database
and verify append-only events, owner isolation, role grants, inbox idempotency,
timer/outbox recovery, snapshots, and projector rebuilds.

## Local Compose backup and isolated recovery

Run from the repository root, with the same `.env`, `COMPOSE_FILE` overrides,
profiles and `COMPOSE_PROJECT_NAME` used by the running stack:

```bash
bash scripts/backup.sh backups/2026-09-07
bash scripts/verify-backup.sh backups/2026-09-07
bash scripts/restore.sh backups/2026-09-07 restore_drill_20260907
```

The backup destination must not exist. The backup reads resolved Compose
configuration in memory to discover the database, admin role and actual named
MinIO volume; it never saves the full config or secrets. It stops running
application services and MinIO for the dump/object snapshot, then restarts
exactly those services in a `finally` handler, including on failure or SIGTERM.
Schedule a maintenance window: API/kernel/other Compose services are unavailable
during this operation. PostgreSQL and Redis remain running. Stop all writers
outside this Compose project first; external writers cannot be fenced by this
script. SIGKILL or host failure cannot run cleanup: inspect the source services
and start the ones recorded by your operations change before ending maintenance.

`manifest.json` records `complete` only after the PostgreSQL custom dump, role
schema (without role passwords), table row counts and local object archive have
been captured and the stopped services restarted. Missing objects, dump/archive
errors or restart failures exit nonzero; incomplete artifacts remain `partial`
and cannot be restored. The manifest includes version, source identifiers,
per-file sizes/SHA-256 hashes and an index of every archived object's content.
`verify-backup.sh` checks these offline and rejects links, path traversal and
incomplete backups. Checksums detect corruption; they do not authenticate an
untrusted backup. Protect the backup directory, including its manifest, in an
access-controlled encrypted backup store. The database dump contains account
hashes and application data. Keep original encryption/signing keys and runtime
secrets separately in your secret manager; they are required for useful recovery.

This workflow deliberately supports only the local named-volume MinIO backend
at `opencitadel-minio:9000`. External MinIO, COS/S3, bind-mounted data and custom
storage need a provider-specific, consistent object snapshot procedure; the
script fails rather than calling a database-only copy a complete backup. Redis
is disposable coordination/cache state and is not backed up. Sandbox ephemeral
files and logs are also excluded. The Helm PostgreSQL CronJob remains a database
backup only, not a complete application recovery procedure.

The restore command first verifies every payload before contacting Docker. It
accepts only a fresh `restore_<name>` target and refuses existing destination
containers or volumes. It creates invocation-specific volumes and an isolated
PostgreSQL container with no network or published ports. It restores roles,
then the custom dump with fail-on-error, compares every table's row count, and
extracts the object archive only into an empty new volume. A second archive is
read back and every restored file's size/hash is compared to the backup.
The recovery container is stopped afterwards; volumes are retained for review.
A failed run retains a `partial` recovery report; use a new target for the next
attempt instead of overwriting it.

`restore-<target>.json` records the exact container/volumes, recovery superuser,
source manifest hash, and `payload_verified` on successful data verification.
It explicitly leaves `application_smoke_verified: false`. This is a recovery
drill, not an automatic production cutover. Inspect its resource identifiers and
attach those volumes to a separately isolated Compose project for application
acceptance. Use the same PostgreSQL/MinIO image versions as the backup and the
application revision that created the data. Reapply runtime role passwords from
your secret manager (roles are restored without passwords), retain the original
encryption/signing keys, configure the restored MinIO endpoint and credentials,
and keep schedulers, integrations and outbound notifications disabled during
the drill. Bind any acceptance UI only to localhost. Do not mount recovery
volumes into the live project or start a new empty application over them.

Before approving cutover, record these checks against the isolated application:

1. A known local user can log in and change their password; prior sessions fail.
2. Existing session history and a representative attachment open correctly.
3. A known knowledge-base document can be retrieved and its stored content read.
4. A previously exported evidence package passes signature and content verification
   using the original signing key (`scripts/verify_evidence_package.py`).
5. Pending execution/approval state can be inspected without dispatching production
   actions; enable integrations only after the recovery is accepted.

The orchestration regression suite runs without a Docker daemon:

```bash
python3 -m unittest discover -s scripts/tests -p test_backup_tooling.py -v
```

Those tests use a recording Docker substitute and synthetic dumps/objects. They
validate failure handling, command arguments, ordering and manifest/content checks;
they do not replace a real Docker/PostgreSQL/MinIO restore and application drill.
Docker must be running and the source PostgreSQL image plus `alpine:3.20` must be
available (Docker may pull missing images) for a real recovery.

## Execution failures and verified recovery

The admin dashboard shows scope projection lag, quarantined Runs, planner retry times,
and durable recovery outcomes. Planner errors retry after 5 and 10 seconds; the third
failure quarantines the Run and creates an in-app notice for its owner/team. Other
Runs continue. Recovery requires an administrator and a written reason. Submit the
owner scope (`user:<id>` or `team:<id>`) in **Execution recovery**, then refresh to see
`pending`, `completed`, `partial`, or `failed`.

Only the kernel rebuilds projections. It blocks new decisions/activities for the
scope, replays original events, verifies state and required decision payloads, and
releases only verified Runs. Missing/corrupt original payloads remain quarantined;
`partial` results list those Runs. Fix the original data or code before retrying.
Recovery is audited and never replays an unknown external write. The kernel-credential
CLI `python -m app.rebuild_execution_projection --scope user:<id>` performs the same
verification and reports how many Run markers it released.

Disabling business scheduling stops fresh schedules, while reconciliation, retention,
connection recycling, notification delivery, rechecks, and recovery continue. Delivery,
recheck, and recovery lanes are independently supervised. The admission ceiling counts
pending and active workflow groups transactionally; children share their parent's
capacity until every member is terminal. A new independent group above the ceiling
receives HTTP 429; retries of an existing group do not consume another slot.

This change targets a freshly initialized greenfield database. Existing initialized
databases do not acquire new columns/tables by rerunning an already-applied initial
migration; do not point a fresh-schema test at production data.
