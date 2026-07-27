[简体中文](README.zh-CN.md)

# OpenCitadel Helm Chart

Helm chart for deploying OpenCitadel on Kubernetes with independent scaling for API and Agent Worker.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.x
- Built and pushed `opencitadel-api` and `opencitadel-worker` images (`api/Dockerfile` multi-stage targets)
- PostgreSQL (pgvector) and Redis reachable from the cluster

## Install

```bash
helm upgrade --install opencitadel ./deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values production-values.yaml \
  --set image.api.repository=your-registry/opencitadel-api \
  --set image.api.tag=latest \
  --set image.worker.repository=your-registry/opencitadel-worker \
  --set image.worker.tag=latest \
  --set replicaCount.api=2 \
  --set replicaCount.worker=2
```

### local mode (in-cluster MinIO)

```bash
helm upgrade --install opencitadel ./deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --set minio.enabled=true \
  --set env.STORAGE_PROVIDER=minio \
  --set secrets.minioAccessKey=minioadmin \
  --set secrets.minioSecretKey=minioadmin
```

When `minio.enabled=true`, the chart deploys a MinIO StatefulSet and sets `MINIO_ENDPOINT` to the in-cluster Service.

## Key values

| Parameter | Default | Description |
|-----------|---------|-------------|
| `replicaCount.api` | 2 | API replica count |
| `replicaCount.worker` | 2 | Worker replica count |
| `autoscaling.api.enabled` | true | API HPA |
| `autoscaling.worker.enabled` | true | Worker HPA |
| `migrate.enabled` | true | API initContainer runs migrations |
| `postgresql.adminUser` | postgres | Bootstrap-only PostgreSQL admin role |
| `postgresql.user` | opencitadel_app | Non-superuser application/migration role subject to RLS |
| `minio.enabled` | false | In-cluster MinIO (set true for local mode) |
| `minio.storage` | 20Gi | MinIO PVC size |
| `env.STORAGE_PROVIDER` | cos | Object storage backend: `cos` or `minio` |
| `env` | see values.yaml | Non-secret env vars (DB/Redis hosts, log level, etc.) |
| `secrets` | see values.yaml | Sensitive values rendered as Secret and injected via `envFrom` |
| `appConfig` | see values.yaml | App behavior config rendered as ConfigMap mounted at `/app/config.yaml` |

> **Note**: Before production, override all sensitive values via `--set` or a dedicated values file:
> `secrets.apiKeySecret`, `secrets.auditSigningKey`, `secrets.jwtSecret`, `secrets.sessionSecret`,
> `secrets.bootstrapAdminPassword`, `secrets.postgresAdminPassword`, `secrets.postgresPassword`,
> and `secrets.redisPassword`. The two PostgreSQL passwords must be distinct.
> Set `env.FRONTEND_BASE_URL`, `env.OAUTH_REDIRECT_BASE`, and `env.COOKIE_SECURE=true` to match your Ingress host.
> `env.USE_DB_APP_CONFIG` defaults to `"true"` for Helm deployments.
> Confirm `env.POSTGRES_HOST` and `env.REDIS_HOST` point to actual in-cluster services.
> The application fails closed in production if its PostgreSQL role is a superuser or has `BYPASSRLS`.

## Production security requirements

- `secrets.apiKeySecret`, `secrets.auditSigningKey`,
  `secrets.jwtSecret`, and `secrets.sessionSecret` must be distinct and at
  least 32 characters. Set their key ids and previous-key JSON maps when
  rotating.
- `secrets.bootstrapAdminPassword` must be at least 12 characters.
  `secrets.postgresAdminPassword`, `secrets.postgresPassword`, and
  `secrets.redisPassword` must be at least 16 characters; the two PostgreSQL
  passwords must differ.
- API, Worker, and the migration initContainer connect as
  `postgresql.user`, a non-superuser role subject to RLS. Production startup
  rejects `rolsuper=true` or `rolbypassrls=true`.
- Keep `networkPolicy.enabled=true`. It restricts sandbox ingress to
  API/Worker and egress to DNS plus public address ranges; private, link-local,
  metadata, and reserved ranges remain blocked.
- Set `env.COOKIE_SECURE=true`, public HTTPS frontend/OAuth URLs, exact
  ingress-controller `env.TRUSTED_PROXY_CIDRS`, approved
  `env.OUTBOUND_ALLOWED_PORTS`, and only exact hostnames in
  `env.OUTBOUND_PRIVATE_HOST_ALLOWLIST`.
- Store `production-values.yaml` in an approved encrypted secret mechanism;
  do not commit it.

## Existing chart-managed PostgreSQL PVC

`/docker-entrypoint-initdb.d` scripts only run for a new data directory. Before
upgrading an existing PVC to the application role, enter a maintenance window
and run this sequence from the repository root:

```bash
NS=opencitadel
RELEASE=opencitadel
CHART=./deploy/helm/opencitadel
VALUES=production-values.yaml
APP_USER=opencitadel_app
PG_POD="$(kubectl -n "$NS" get pod \
  -l app.kubernetes.io/component=postgres \
  -o jsonpath='{.items[0].metadata.name}')"

# 1. Stop writers and back up the current database.
kubectl -n "$NS" scale deployment \
  "${RELEASE}-api" "${RELEASE}-worker" --replicas=0
kubectl -n "$NS" exec "$PG_POD" -- sh -ceu \
  'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "${RELEASE}-before-app-role-$(date +%Y%m%d%H%M%S).sql"

# 2. Apply the new Secret and script ConfigMap without starting migrations.
helm template "$RELEASE" "$CHART" \
  --namespace "$NS" --values "$VALUES" \
  --show-only templates/secret.yaml \
  --show-only templates/configmap-postgres-init.yaml \
  | kubectl -n "$NS" apply -f -

# 3. Copy the exact checked-in script; do not paste an edited variant.
kubectl -n "$NS" cp \
  deploy/helm/opencitadel/files/postgres/init-app-role.sh \
  "$PG_POD:/tmp/init-app-role.sh"
APP_PASSWORD="$(kubectl -n "$NS" get secret "${RELEASE}-secret" \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 --decode)"
kubectl -n "$NS" exec "$PG_POD" -- chmod 0500 /tmp/init-app-role.sh

# 4. Create/alter the non-bypass app role and transfer relation ownership.
kubectl -n "$NS" exec "$PG_POD" -- env \
  OPENCITADEL_APP_USER="$APP_USER" \
  OPENCITADEL_APP_PASSWORD="$APP_PASSWORD" \
  /tmp/init-app-role.sh

# 5. rolsuper and rolbypassrls must be false; wrong_owner must be 0.
kubectl -n "$NS" exec -i "$PG_POD" -- env \
  OPENCITADEL_APP_USER="$APP_USER" sh -ceu '
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v app_user="$OPENCITADEL_APP_USER"
  ' <<'SQL'
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname = :'app_user';
SELECT count(*) AS wrong_owner
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
  AND pg_get_userbyid(c.relowner) <> :'app_user';
SQL
unset APP_PASSWORD

# 6. Upgrade only after role/ownership verification succeeds.
helm upgrade "$RELEASE" "$CHART" \
  --namespace "$NS" --values "$VALUES"
```

This procedure applies only to the chart-managed StatefulSet. For external
PostgreSQL, execute
`deploy/helm/opencitadel/files/postgres/init-app-role.sh` through the
provider's approved admin channel before Helm starts the migration
initContainer.

## Production verification

```bash
# NetworkPolicy must render and exist.
helm template opencitadel ./deploy/helm/opencitadel \
  --namespace opencitadel --values production-values.yaml \
  --show-only templates/networkpolicy-sandbox.yaml
kubectl -n opencitadel get networkpolicy opencitadel-sandbox

# Migrations complete before API readiness; all workloads become available.
kubectl -n opencitadel rollout status deployment/opencitadel-api
kubectl -n opencitadel rollout status deployment/opencitadel-worker
kubectl -n opencitadel exec deployment/opencitadel-api -- \
  curl --fail http://127.0.0.1:8000/api/status
```

Repeat the `rolsuper`, `rolbypassrls`, and `wrong_owner` query after every
database-role change. For encryption/audit key rotation and chain verification,
follow the [production deployment
guide](../../../docs/operations/deployment.md#credential-encryption-and-audit-signing-key-rotation).

## Release images

Tagged releases (`v*`) publish multi-arch images to `ghcr.io/ocealong/opencitadel-{api,worker,migrate,ui,sandbox}` via [`.github/workflows/release.yml`](../../../.github/workflows/release.yml). Override `image.*.repository` and `image.*.tag` to consume release builds.

## Architecture

- **API Deployment**: Stateless FastAPI, SSE connection layer
- **Worker Deployment**: Consumes Redis dispatch queue, runs agents
- **migrate initContainer**: `python -m app.migrate`, equivalent to docker-compose `opencitadel-migrate`

## Scaling

```bash
# Manually scale Worker replicas (agent load)
kubectl scale deployment opencitadel-worker --replicas=4 -n opencitadel

# Or enable HPA (autoscaling.worker.enabled=true in values.yaml)
```

## Architecture evolution

After a stable single-node Compose deployment, split compute and sandbox execution in phases—see [Architecture evolution guide](../../../docs/architecture/architecture-evolution.md).

Recommended order:

1. Externalize PostgreSQL / Redis (free memory on the primary node)
2. Deploy API + Worker with this chart (HPA on queue depth or CPU)
3. Point `sandbox.address` at a remote sandbox cluster (Worker no longer mounts docker.sock)

## Related docs

- Root [README.md](../../../README.md) — architecture and configuration
- [Production deployment guide](../../../docs/operations/deployment.md) — production deployment guide
- [Architecture evolution guide](../../../docs/architecture/architecture-evolution.md) — scale-out and external sandbox
- [api/README.md](../../../api/README.md) — local API / Worker development
