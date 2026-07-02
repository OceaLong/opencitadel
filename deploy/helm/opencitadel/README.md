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
| `minio.enabled` | false | In-cluster MinIO (set true for local mode) |
| `minio.storage` | 20Gi | MinIO PVC size |
| `env.STORAGE_PROVIDER` | cos | Object storage backend: `cos` or `minio` |
| `env` | see values.yaml | Non-secret env vars (DB/Redis hosts, log level, etc.) |
| `secrets` | see values.yaml | Sensitive values rendered as Secret and injected via `envFrom` |
| `appConfig` | see values.yaml | App behavior config rendered as ConfigMap mounted at `/app/config.yaml` |

> **Note**: Before production, override all sensitive values via `--set` or a dedicated values file:
> `secrets.apiKeySecret`, `secrets.jwtSecret`, `secrets.sessionSecret`, `secrets.bootstrapAdminPassword`, `secrets.postgresPassword`.
> Set `env.FRONTEND_BASE_URL`, `env.OAUTH_REDIRECT_BASE`, and `env.COOKIE_SECURE=true` to match your Ingress host.
> `env.USE_DB_APP_CONFIG` defaults to `"true"` for Helm deployments.
> Confirm `env.POSTGRES_HOST` and `env.REDIS_HOST` point to actual in-cluster services.

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
