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
For production, external managed services are recommended.

## Install

Create a protected values file with unique secrets and image coordinates, then:

```bash
helm lint deploy/helm/opencitadel
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml \
  --set image.api.repository=REGISTRY/opencitadel-api \
  --set image.executionKernel.repository=REGISTRY/opencitadel-execution-kernel \
  --set image.ui.repository=REGISTRY/opencitadel-ui \
  --set image.sandbox.repository=REGISTRY/opencitadel-sandbox
```

Use `minio.enabled=true` with `env.STORAGE_PROVIDER=minio` only when an
in-cluster object store is intended.

## Runtime topology

| Workload | Responsibility | PostgreSQL role |
| --- | --- | --- |
| API | HTTP, auth, admission, public SSE | `postgresql.user` |
| Migration init | sole greenfield Alembic revision and initial config seed | `postgresql.migrationUser` |
| Execution kernel | commands, decisions, Activities, timers, outbox, projectors, scheduler | `executionKernel.databaseUser` |
| UI | Next.js application | none |

Every migration invocation acquires the same PostgreSQL advisory lock across
schema upgrade and initial seed, so concurrent API initContainers serialize.
API and kernel credentials cannot migrate the schema; the kernel has only the
append/claim/projection grants required by its runtime role.

## Important values

| Parameter | Default | Description |
| --- | --- | --- |
| `replicaCount.api` | `2` | API replicas |
| `executionKernel.replicas` | `2` | execution-kernel replicas |
| `executionKernel.databaseUser` | `opencitadel_execution_kernel_runtime` | dedicated kernel role |
| `executionKernel.metricsPort` | `9108` | internal Prometheus port |
| `shutdown.timeoutSeconds` | `30` | bounded application task drain |
| `shutdown.terminationGracePeriodSeconds` | `45` | pod grace; must exceed drain timeout |
| `autoscaling.api.enabled` | `true` | API HPA |
| `autoscaling.executionKernel.enabled` | `true` | kernel HPA |
| `postgresql.enabled` | `true` | chart-managed greenfield PostgreSQL |
| `redis.enabled` | `true` | chart-managed Redis |
| `minio.enabled` | `false` | optional chart-managed MinIO |
| `networkPolicy.enabled` | `true` | workload network isolation |
| `opsCollector.enabled` | `false` | fixed read-only Patrol Collector |
| `opsActuator.enabled` | `false` | allowlisted write-only Patrol Actuator |
| `migrate.enabled` | `true` | run the serialized migration initContainer |

The schema in `values.schema.json` validates the execution-kernel contract and
rejects obsolete deployment keys.

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

Use an approved secret manager rather than committing a production values file.
The PostgreSQL administrator credential is bootstrap-only and is not injected
into API or execution-kernel containers.

## Security requirements

- Keep `networkPolicy.enabled=true` and scope sandbox ingress to API/kernel.
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
