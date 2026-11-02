# Deployment

[简体中文](deployment.zh-CN.md)

OpenCitadel deploys one stateless API and one database-authoritative execution
kernel. PostgreSQL is required; Redis only lowers wake-up latency. The shipped
schema uses one greenfield Alembic lineage from `0001greenfield` through
`0030evaluation_judge_history`, including incremental execution, analysis, and
evaluation migrations. Deploy into a fresh database; `app.migrate` upgrades the
lineage to its current head rather than importing an earlier development catalog.

## Processes

| Process          | Compose service                | Database credential                   |
| ---------------- | ------------------------------ | ------------------------------------- |
| Migration        | `opencitadel-migrate`          | `POSTGRES_MIGRATION_*`                |
| API              | `opencitadel-api`              | `POSTGRES_USER` / `POSTGRES_PASSWORD` |
| Execution kernel | `opencitadel-execution-kernel` | `POSTGRES_KERNEL_*`                   |
| UI               | `opencitadel-ui`               | none                                  |

The PostgreSQL administrator credential is reserved for bootstrap and the optional Helm database backup job. API and kernel runtime containers must not receive it. The kernel runs command inbox, Run decisions, Activities,
timers, outbox delivery, formal projectors, automation, and maintenance ticks.
There is no second execution service.

Each role loads deployment settings once and constructs only its own manual
typed graph: the API owns `ApiRuntime`, while the execution process owns
`KernelRuntime`. Their `TaskSupervisor` instances and PostgreSQL, Redis,
storage, provider, and connection-pool resources are never shared.

## Compose quick start

```bash
make quickstart
```

The first run creates a private `.env` (mode `0600`), generates independent
random keys and passwords, builds the sandbox image, and starts PostgreSQL,
Redis, MinIO, API, execution kernel, UI, and Nginx. After readiness succeeds,
the terminal shows the initial administrator login. Open `http://localhost:8088`.
Subsequent runs preserve the existing configuration and credentials. A failed
health check exits with an error and diagnostic commands.

To prepare configuration without starting containers, run `make configure`.
The short [`.env.example`](../../.env.example) contains only basic choices;
[the complete reference](../../deploy/config/.env.reference) groups optional
integrations, external services, secret rotation, and advanced tuning. Copy
only the overrides you need into `.env`; the reference is not loaded at runtime.
Existing environment variable names and explicit overrides remain supported.

When upgrading an installation whose `OPENCITADEL_ENV_FILE` incorrectly pointed
to `.env.example`, quickstart repairs that selector. Before restarting an
existing database, preserve the encryption/signing credentials actually used
by the previous processes in `.env`, using your original secret backup or
running deployment's secret source. Values that were previously ignored cannot
recover encrypted data by being newly activated; never regenerate credentials
for an existing database.

| Category                       | What the operator supplies                                                      |
| ------------------------------ | ------------------------------------------------------------------------------- |
| Basic startup                  | Optional site origin, project name, profiles, deployment mode, storage provider |
| Generated credentials          | Nothing on a fresh local install; retain and back up `.env`                     |
| Built-in services              | Database, Redis, MinIO and sandbox addresses are derived by Compose             |
| Optional integrations          | COS, external services, OAuth or demo inference inputs when used                |
| Advanced tuning                | Pool sizes, concurrency, network exceptions and timeouts only when needed       |
| Runtime behavior and inference | **Settings → Runtime policy / Inference**, stored in PostgreSQL                 |

`FRONTEND_BASE_URL` is the public site origin. OAuth redirect and secure-cookie
defaults follow it. `make quickstart` also derives Nginx hostname, TLS mode and
host ports from it; existing `OAUTH_REDIRECT_BASE`, `COOKIE_SECURE`,
`OPENCITADEL_DOMAIN`, `HTTPS_ENABLED`, `NGINX_PORT` and `NGINX_HTTPS_PORT`
overrides take precedence. Direct `docker compose up` with a custom host port
requires the matching advanced port override. Production uses `ENV=production`,
a real HTTPS origin, and operator-provided certificates; see
[HTTPS setup](https-domain-setup.md). Trusted proxy CIDRs and external network
exceptions still describe the actual deployment.

New local installs generate these independent credentials automatically:

- `POSTGRES_ADMIN_PASSWORD`, `POSTGRES_MIGRATION_PASSWORD`,
  `POSTGRES_PASSWORD`, `POSTGRES_KERNEL_PASSWORD`, `REDIS_PASSWORD`
- `API_KEY_SECRET`, `AUDIT_SIGNING_KEY`, `JWT_SECRET`, `SESSION_SECRET`,
  `DATABASE_AUTHORIZATION_SIGNING_SECRET`, `PUBLIC_CURSOR_SECRET`
- `SANDBOX_BROKER_TOKEN`, `SANDBOX_TOKEN_SEED`, `MINIO_SECRET_KEY`
- `OPS_COLLECTOR_TOKEN`, `OPS_ACTUATOR_TOKEN`, `METRICS_TOKEN`,
  `BOOTSTRAP_ADMIN_PASSWORD`

Usernames (`POSTGRES_ADMIN_USER`, `POSTGRES_USER`), key IDs (`API_KEY_SECRET_ID`,
`AUDIT_SIGNING_KEY_ID`), rotation maps and `OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS`
have defaults. `SANDBOX_TOKEN_SEED` supplies the API and kernel's per-sandbox
data-plane tokens. Ops servers validate their own tokens when enabled, so
unused profiles do not block a basic Compose render. Cloud storage remains
available via `STORAGE_PROVIDER=cos` and the `COS_*` overrides.

Keep cryptographic keys at least 32 random bytes. In production,
`TRUSTED_PROXY_CIDRS` rejects broad RFC1918 ranges overlapping sandbox/pod
networks; configure precise proxy CIDRs, `OUTBOUND_ALLOWED_PORTS` and
`OUTBOUND_PRIVATE_HOST_ALLOWLIST` as needed.

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

To recreate a default local installation's containers while keeping its state,
retain `.env` and use the same Compose project:

```bash
docker compose down
make quickstart
```

Keep the named volumes; `down --volumes` removes installation data. For a custom
environment file or project, use the same selector and project for both commands.
Migration runs again, but an existing administrator and Runtime Policy head are
preserved. Initial generated login information is shown only after the first
successful startup.

Local verification on 2026-10-10 covered a fresh `make quickstart`, removal of all
project containers while retaining volumes, and a second `make quickstart`.
Both runs reached API liveness/readiness and served the UI. The original HTTP
login cookie remained valid; stored credentials, administrator identity, and
the PostgreSQL-seeded basic KB policy revision remained unchanged. This verifies
that local startup and recovery path, not a portable capacity benchmark or the
complete acceptance suite.

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

The application drain defaults to `OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS=90`.
Compose uses `stop_grace_period: 120s`; Helm uses `shutdown.timeoutSeconds: 90`
and `shutdown.terminationGracePeriodSeconds: 120`. The platform termination
grace must always be greater than the application timeout.

## Storage and sandbox

Production object storage must be shared by every API and kernel replica. Use
COS or S3-compatible MinIO with a private bucket; local filesystem storage is
not a multi-replica option.

Compose isolates Docker access in `opencitadel-sandbox-broker`. API and kernel
receive only its narrow token-authenticated HTTP endpoint, never the Docker
socket. Quickstart detects the socket group on native Linux; use `DOCKER_SOCK_GID`
for an explicit override with a custom daemon. Kubernetes
uses a dedicated execution-kernel ServiceAccount and restricted sandbox Pod
RBAC. Keep the Squid sandbox egress proxy enabled; its static configuration uses private-range/metadata denials and Safe_ports, without a domain allowlist. Each sandbox's
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
is independently generated on new installs. When unset, it still defaults to
`SESSION_SECRET` for compatibility with existing deployments and their seeded
RLS `app.rls_signing_secret`. The
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

Compose and Helm share the same initial policy seed. A fresh database uses text
extraction and keyword search for knowledge bases, with vector search,
reranking, GraphRAG, and OCR off. Only chat inference is needed for initial
Ask/Agent use; text indexing itself requires no inference model. Configure
optional bindings and enable their fields under **Settings → Runtime policy**
when needed. Upgrades preserve an existing policy head and history. See the
[knowledge tutorial](../tutorials/02-internal-knowledge-base.md) before enabling
enhancements or rebuilding existing libraries.

## Observability

Local initialization generates `METRICS_TOKEN` for authenticated API metrics;
external deployment can supply an explicit value. Set
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
helm lint deploy/helm/opencitadel --set publicUrl=https://citadel.example.com
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --set publicUrl=https://citadel.example.com
```

The public HTTPS origin is the only required value for a self-contained first
install. The chart defaults to bundled PostgreSQL, Redis and MinIO, derives
service hosts and the sandbox namespace from the release, and generates
independent credentials in role-specific Secrets. Upgrades reuse these Secrets;
keep them with database and object-storage backups. Helm NOTES explains how
to retrieve the initial administrator login. See the [chart guide](../../deploy/helm/opencitadel/README.md)
for ingress/TLS, external services, and secret-manager overrides. Keep
`networkPolicy.enabled=true`, separate API/kernel/migration database users,
and configure `executionKernel.replicas` plus its HPA for Activity volume.
Optional Ops Collector and Actuator workloads must remain network-separated;
the Actuator is reachable only from API/kernel and still requires a persisted
approval. Their RBAC is a namespaced `Role`/`RoleBinding` rendered per allowed
namespace, not a cluster-wide `ClusterRole`.

These templates have separate deployment switches: NetworkPolicy and egress proxy default on; `pdb.enabled`, `backup.enabled`, `monitoring.serviceMonitor.enabled`, and `monitoring.prometheusRule.enabled` default off. The backup CronJob also requires chart-managed PostgreSQL. Squid uses the static address/port ACLs; `egressProxy.allowedDomains` is not consumed. Alerts require Prometheus Operator CRDs, matching selectors, and actual scrapes; API scraping uses a generated strong token when ServiceMonitor is enabled, or an explicit `secrets.metricsToken` override. Compose Nginx templates set CSP/nosniff and HTTPS HSTS. Helm supplies security headers only through optional Ingress annotations for ingress-nginx, subject to controller support.

For chart-managed PostgreSQL, `files/postgres/init-app-role.sh` creates the
distinct migration, API, and kernel roles before the greenfield migration.
For an external database, provision equivalent roles before install. Verify
that runtime roles return `rolsuper=false` and `rolbypassrls=false`; never give
schema ownership or migration credentials to API or kernel containers.

## Release artifacts and supply chain

Release tags publish eight images: `api`, `execution-kernel`, `migrate`,
`sandbox-broker`, `ui`, `sandbox`, `ops-collector`, and `ops-actuator`. The
`.github/workflows/security.yml` gate runs Gitleaks, CodeQL, and Trivy. The
release workflow scans every image before publish and attaches an SBOM plus
signed provenance. Deploy by immutable digest after verifying provenance,
rather than relying on `latest`.

The deterministic inference provider under `e2e/fixtures/` is not a release
artifact. It exists only in the Compose `acceptance` profile and must never be
added to Helm, Kustomize, quickstart, production settings, or the release image
matrix.

## Evaluation runtime deployment

The same kernel owns four critical lanes: `evaluation-scheduler`, `evaluation-reconciler`, `evaluation-scoring`, and `evaluation-cleanup`; API work is authorization, validation, and durable admission. An unexpected lane failure withdraws kernel readiness and requests shutdown. Recorded/isolated subjects and Judges use formal Runs, without another execution service. Default deployments enable no controlled physical-environment adapter. The local Docker adapter requires a non-production `ENV`, `EVALUATION_LOCAL_DOCKER_ENABLED=true`, a read-only administrator inventory at `EVALUATION_TEST_INVENTORY_PATH`, and configured physical budget inventory. Acceptance inventory mounts and the broker journal volume belong to its dedicated override, not production. See the [evaluation control plane](../architecture/evaluation-control-plane.md) and [controlled environments](../evaluation-environments.md).

## Deterministic acceptance gate

Run the same full-stack acceptance entrypoint used by CI:

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

The full execution gate also requires `ACCEPTANCE_CAPACITY_REPORT` and
`ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST` with valid AC21 measured evidence and the
original private `OfflineProofContext`. The normal CLI does not construct that
context, and the full reference-round reuse and native collector remain
incomplete. AC21 is retained as a known failure; no report, fixture, or smaller
workload is substituted to make it pass. See the
[capacity tooling status](../../scripts/execution_capacity/README.md).

The current CI workflow reports the incomplete acceptance plane without blocking
the job. This does not change failed or skipped requirements into passes.
Deployment verification and other passing cases do not establish an all-green
release gate.

## Release gates

Before rollout, run:

The API command below excludes only the six current-invocation consumers in
`test_execution_visualization_closed_loop.py`. The acceptance runner executes
them after validating the native strict report and restoration receipt, retaining
`strict-pytest.xml` and requiring six passes with zero skips.

```bash
cd api
uv run pytest -q --ignore=tests/app/integration/test_execution_visualization_closed_loop.py
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
helm lint deploy/helm/opencitadel --set publicUrl=https://citadel.example.com
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

External writes have separate operational signals. `execution_effect_rows` and
`execution_effect_oldest_age_seconds` read durable operations; age is measured
from operation creation, including earlier preparation. Request gauges group
query, human resolution and abandonment by status. Query observations and human
resolution gauges retain recorded `pending`, `unknown`, `applied`, or `not_applied`
facts across collector restarts. These are retained-row gauges, not lifetime counters.
The optional Helm alerts distinguish an old unknown operation from newly failed
verification infrastructure. Neither proves a business write failed, and neither
permits replaying it. Use the original operation query or evidence-backed human
resolution in the workbench. A new successful query does not erase prior failed
query history or release physical accounting holds. Embedding refusal emits the
bounded `embedding_space_mismatch` log with `model_identity` or `descriptor_changed`,
without credentials or content; rebuild the affected vectors under a verified
descriptor rather than mixing dimensions-compatible spaces.

`execution_effect_integrity_conflict_rows` counts retained operations with
contradictory outcomes or business results. The optional
`OpencitadelEffectIntegrityConflict` alert requires evidence investigation.
The workbench explains the quarantine and disables ordinary resolution actions;
a matching late receipt cannot lift it. Cancel the Run to stop waiting if needed,
without resending the write or clearing its accounting holds.

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
migration. `app.migrate` applies pending revisions in the current lineage to its
head; do not point a fresh-schema test at production data.
