# OpenCitadel v2 Helm Chart

[English](README.md)

该 Chart 部署核心 API、执行内核、UI、PostgreSQL/pgvector、Redis、可选 MinIO、
沙箱出站代理、迁移 Init Container 和 Namespace 级沙箱 RBAC。

请从 Secret Manager 提供 `secrets` 下的全部值，并覆盖镜像仓库与标签。这是破坏式
绿色部署，只运行 Alembic Revision `0001greenfield`。

```bash
helm lint deploy/helm/opencitadel -f production-values.yaml
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  -f production-values.yaml
```

保持 `networkPolicy.enabled=true`。执行内核 ServiceAccount 只能在发布 Namespace 内
创建/删除沙箱 Pod；沙箱 Pod 不挂载 ServiceAccount Token，并且只能访问 DNS 与受控
出站代理。
