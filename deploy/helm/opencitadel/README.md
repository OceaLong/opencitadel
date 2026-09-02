# OpenCitadel v2 Helm chart

[简体中文](README.zh-CN.md)

This chart deploys the core API, execution kernel, UI, PostgreSQL/pgvector,
Redis, optional MinIO, sandbox egress proxy, migration init container, and
namespace-scoped sandbox RBAC.

Supply all values under `secrets` from a secret manager and override image
repositories/tags. The release is a destructive greenfield deployment and
runs only Alembic revision `0001greenfield`.

```bash
helm lint deploy/helm/opencitadel -f production-values.yaml
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  -f production-values.yaml
```

Keep `networkPolicy.enabled=true`. The execution-kernel ServiceAccount may
create/delete only sandbox Pods in the release namespace; sandbox Pods receive
no ServiceAccount token and can reach only DNS and the controlled egress proxy.
