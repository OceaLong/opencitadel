[简体中文](README.zh-CN.md)

# OpenCitadel Helm Chart

This chart deploys the greenfield OpenCitadel runtime: API, universal execution
kernel, UI, sandbox integration, PostgreSQL/Redis options, and optional Ops
Collector/Actuator.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.x
- `opencitadel-api`, `opencitadel-execution-kernel`, `opencitadel-ui`, and
  `opencitadel-sandbox` images
- A new PostgreSQL database with pgvector, plus Redis

The chart can create PostgreSQL, Redis, and MinIO for a self-contained install.
The built-in PostgreSQL and Redis are single-replica, evaluation-grade only; for
production use an external/managed service or an operator such as CloudNativePG,
and set `postgresql.enabled=false` / `redis.enabled=false` with `env.POSTGRES_HOST`,
`env.REDIS_HOST` (and related credentials in `secrets.*`) pointing at it.

## Install

Create a protected values file with unique secrets and image coordinates, then:

```bash
helm lint deploy/helm/opencitadel --values values.production.yaml
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml \
  --set image.api.repository=REGISTRY/opencitadel-api \
  --set image.executionKernel.repository=REGISTRY/opencitadel-execution-kernel \
  --set image.ui.repository=REGISTRY/opencitadel-ui \
  --set image.sandbox.repository=REGISTRY/opencitadel-sandbox
```

Before install, set `env.SANDBOX_K8S_NAMESPACE: opencitadel` (or your release namespace) to align sandbox Role, quota, and NetworkPolicy. Its default `default` does not follow `--namespace`. COS needs bucket/region and credentials; in-cluster or external MinIO needs a private bucket and distinct strong credentials. Set frontend/OAuth URLs to the real HTTPS origin; Ingress defaults off.

Use `minio.enabled=true` with `env.STORAGE_PROVIDER=minio` only when an
in-cluster object store is intended.

## Runtime topology

| Workload         | Responsibility                                                              | PostgreSQL role                |
| ---------------- | --------------------------------------------------------------------------- | ------------------------------ |
| API              | HTTP, auth, admission, public SSE                                           | `postgresql.user`              |
| Migration init   | greenfield Alembic lineage upgraded to current head and initial config seed | `postgresql.migrationUser`     |
| Execution kernel | commands, decisions, Activities, timers, outbox, projectors, scheduler      | `executionKernel.databaseUser` |
| UI               | Next.js application                                                         | none                           |

Every migration invocation acquires the same PostgreSQL advisory lock across
schema upgrade and initial seed, so concurrent API initContainers serialize.
API and kernel credentials cannot migrate the schema; the kernel has only the
append/claim/projection grants required by its runtime role.

## Important values

| Parameter                                | Default                                | Description                                                        |
| ---------------------------------------- | -------------------------------------- | ------------------------------------------------------------------ |
| `replicaCount.api`                       | `2`                                    | API replicas                                                       |
| `executionKernel.replicas`               | `2`                                    | execution-kernel replicas                                          |
| `executionKernel.databaseUser`           | `opencitadel_execution_kernel_runtime` | dedicated kernel role                                              |
| `executionKernel.metricsPort`            | `9108`                                 | internal Prometheus port                                           |
| `shutdown.timeoutSeconds`                | `30`                                   | bounded application task drain                                     |
| `shutdown.terminationGracePeriodSeconds` | `45`                                   | pod grace; must exceed drain timeout                               |
| `autoscaling.api.enabled`                | `true`                                 | API HPA                                                            |
| `autoscaling.executionKernel.enabled`    | `true`                                 | kernel HPA                                                         |
| `postgresql.enabled`                     | `true`                                 | chart-managed greenfield PostgreSQL                                |
| `redis.enabled`                          | `true`                                 | chart-managed Redis                                                |
| `minio.enabled`                          | `false`                                | optional chart-managed MinIO                                       |
| `networkPolicy.enabled`                  | `true`                                 | workload network isolation                                         |
| `egressProxy.enabled`                    | `true`                                 | sandbox egress proxy (squid) required by the sandbox NetworkPolicy |
| `pdb.enabled`                            | `false`                                | PodDisruptionBudget (minAvailable:1) for api/kernel                |
| `topologySpread.enabled`                 | `true`                                 | spread api/kernel replicas across nodes                            |
| `monitoring.prometheusRule.enabled`      | `false`                                | render baseline PrometheusRule alerts                              |
| `backup.enabled`                         | `false`                                | scheduled pg_dump CronJob to a PVC                                 |
| `opsCollector.enabled`                   | `false`                                | fixed read-only Patrol Collector                                   |
| `opsActuator.enabled`                    | `false`                                | allowlisted Patrol writes and capability discovery                 |
| `migrate.enabled`                        | `true`                                 | run the serialized migration initContainer                         |

The schema in `values.schema.json` validates the execution-kernel contract and
rejects obsolete deployment keys.

## Resilience and observability

- `pdb.enabled=true` keeps at least one api/kernel pod during voluntary
  disruptions; use it only with multiple replicas.
- `topologySpread.enabled` (default on) spreads api/kernel across nodes with a
  soft (`ScheduleAnyway`) hostname constraint, so single-node clusters still
  schedule.
- `monitoring.prometheusRule.enabled=true` renders a `PrometheusRule` (requires
  the Prometheus Operator) with baseline alerts: approval-decision timeout rate,
  audit-chain verification failure, execution outbox lag/redelivery backlog,
  sandbox admission rejection rate, HTTP 5xx rate, and rate-limit rejection rate.
  API instrumentation implements `http_requests_total` and `rate_limit_rejected_total`; the rules require actual scrapes to have data.
- `backup.enabled=true` and `postgresql.enabled=true` render the `<release>-postgres-backup` CronJob/PVC with retained `pg_dump` files. This template exposes no HTTP status endpoint: `opsCollector.registeredBackups` still needs a separately provided `status_url`, not a CronJob name. The PVC dump covers only the database and is evaluation-grade; production needs consistent database/object-storage backup.

Enable `monitoring.serviceMonitor.enabled=true` and set a strong `secrets.metricsToken` to scrape `/api/metrics` with bearer auth; an empty token returns 404. The ServiceMonitor currently scrapes only API; configure a separate Prometheus scrape for the kernel metrics Service on 9108. Match monitoring CRDs, selectors, and metrics network policies to your Prometheus deployment.

## Required secrets

Override every placeholder. In particular, use distinct values for:

- `secrets.postgresAdminPassword`
- `secrets.postgresMigrationPassword`
- `secrets.postgresPassword`
- `secrets.executionKernelPostgresPassword`
- `secrets.redisPassword`
- `secrets.apiKeySecret`, `secrets.auditSigningKey`, `secrets.jwtSecret`, and
  `secrets.sessionSecret`
- `secrets.bootstrapAdminPassword`
- `secrets.sandboxTokenSeed`
- `opsCollector.token` / `opsActuator.token` (required when enabled; at least 32 characters)

Use an approved secret manager rather than committing a production values file.
The PostgreSQL administrator credential is reserved for bootstrap and the optional database backup job; it is not injected into API or execution-kernel containers.

## Security requirements

- Keep `networkPolicy.enabled=true` and scope sandbox ingress to API/kernel.
- Keep `egressProxy.enabled=true`: it deploys the squid egress proxy that every
  sandbox must traverse for outbound traffic. The sandbox NetworkPolicy allows
  only DNS plus port 3128 to the proxy Pod (label
  `app.kubernetes.io/component=egress-proxy`), so the proxy resolves each
  destination and enforces the private-range/metadata blacklist from
  `deploy/squid/squid.conf`. The api/kernel `SANDBOX_HTTP_PROXY`,
  `SANDBOX_HTTPS_PROXY`, and `SANDBOX_CHROME_ARGS` default to
  `http://<release>-egress-proxy:3128`. Disabling it fail-closes all sandbox
  egress except DNS For an external proxy, override `env.SANDBOX_*` and update NetworkPolicy to permit that exact proxy destination. Squid currently uses address denials and Safe_ports; the reserved `egressProxy.allowedDomains` value has no effect.
- Keep the Collector read-only and the Actuator separately allowlisted.
- Set public HTTPS frontend/OAuth URLs and `env.COOKIE_SECURE=true`.
- Set exact trusted proxy CIDRs, outbound ports, and private-host allowlists.
- Do not grant `SUPERUSER` or `BYPASSRLS` to any runtime database role.
- Deploy into a new database. This chart contains no catalog conversion path.

Chart-managed PostgreSQL runs `files/postgres/init-app-role.sh` only during
fresh database initialization. It creates distinct migration, API, and kernel
roles before Alembic runs. For an external new database, provision equivalent
roles first and verify them with:

```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('opencitadel_app', 'opencitadel_execution_kernel_runtime');
```

The dedicated migration login also needs `CREATE` on the application database to validate fixed DDL in a temporary scratch schema. This grant does not include `CREATEDB`, `SUPERUSER`, `BYPASSRLS`, or a database grant option; API and kernel logins must not receive database `CREATE`. Existing installations require an explicit operator-reviewed role provisioning update before migrations; application startup does not elevate roles automatically.

Scale through `autoscaling.executionKernel.minReplicas/maxReplicas` or replica values; HPA defaults on and can overwrite manual `kubectl scale`. API/kernel use separate process readiness/liveness probes; Collector/Actuator TCP probes prove listener reachability only. The kernel includes four critical evaluation lanes whose unexpected failure withdraws readiness; controlled local physical environments default off. Full AC21 capacity acceptance still requires measured evidence and remains incomplete.

## Scaling and verification

```bash
kubectl -n opencitadel rollout status deployment/opencitadel-api
kubectl -n opencitadel rollout status deployment/opencitadel-execution-kernel
kubectl -n opencitadel scale deployment/opencitadel-execution-kernel --replicas=4

helm template opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --values values.production.yaml >/dev/null
kubectl -n opencitadel get networkpolicy
```

Release tags publish
`ghcr.io/ocealong/opencitadel-{api,execution-kernel,migrate,ui,sandbox,ops-collector,ops-actuator}`.

See the [deployment guide](../../../docs/operations/deployment.md),
[execution-kernel architecture](../../../docs/architecture/execution-kernel.md),
and [Ops Patrol runbook](../../../docs/operations/ops-patrol.md).

Artifact upload cleanup intents use a separate runtime PostgreSQL pool (up to two
connections per process, no overflow). The same application/kernel database role
and signed request authorization apply. The pool has a 10-second acquisition
limit and each intent write has a 10-second overall deadline; runtime shutdown
disposes it. Budget these connections in addition to the ordinary pool. This
avoids waiting for a second ordinary connection while holding an artifact lock.
