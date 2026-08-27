[English](README.md)

# OpenCitadel Helm Chart

本 Chart 部署全新的 OpenCitadel 运行时：API、统一执行内核、UI、沙箱接入、
PostgreSQL/Redis 选项，以及可选的 Ops Collector/Actuator。

## 前置条件

- Kubernetes 1.24+
- Helm 3.x
- `opencitadel-api`、`opencitadel-execution-kernel`、`opencitadel-ui`、
  `opencitadel-sandbox` 镜像
- 带 pgvector 的全新 PostgreSQL 数据库和 Redis

Chart 可为自包含安装创建 PostgreSQL、Redis 与 MinIO；生产环境建议使用托管服务。

## 安装

先创建受保护的 Values 文件，配置互不相同的密钥与镜像地址，然后执行：

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

只有确定使用集群内对象存储时，才设置 `minio.enabled=true` 与
`env.STORAGE_PROVIDER=minio`。

## 运行拓扑

| Workload | 职责 | PostgreSQL 角色 |
| --- | --- | --- |
| API | HTTP、认证、准入、公开 SSE | `postgresql.user` |
| Migration init | 唯一绿地 Alembic Revision 与首次配置 Seed | `postgresql.migrationUser` |
| 执行内核 | Command、决策、Activity、Timer、Outbox、投影、Scheduler | `executionKernel.databaseUser` |
| UI | Next.js 应用 | 无 |

每个迁移调用都在 Schema Upgrade 与首次 Seed 的完整区间持有同一个 PostgreSQL
Advisory Lock，因此多个 API initContainer 会自动串行。API 与执行内核凭证不能迁移
Schema；内核只有 Append、Claim 与 Projection 所需的运行权限。

## 主要 Values

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `replicaCount.api` | `2` | API 副本数 |
| `executionKernel.replicas` | `2` | 执行内核副本数 |
| `executionKernel.databaseUser` | `opencitadel_execution_kernel_runtime` | 专用内核角色 |
| `executionKernel.metricsPort` | `9108` | 集群内 Prometheus 端口 |
| `shutdown.timeoutSeconds` | `30` | 应用任务有界排空时间 |
| `shutdown.terminationGracePeriodSeconds` | `45` | Pod 宽限期，必须大于排空时间 |
| `autoscaling.api.enabled` | `true` | API HPA |
| `autoscaling.executionKernel.enabled` | `true` | 执行内核 HPA |
| `postgresql.enabled` | `true` | Chart 托管的全新 PostgreSQL |
| `redis.enabled` | `true` | Chart 托管 Redis |
| `minio.enabled` | `false` | 可选的 Chart 托管 MinIO |
| `networkPolicy.enabled` | `true` | Workload 网络隔离 |
| `opsCollector.enabled` | `false` | 固定只读 Patrol Collector |
| `opsActuator.enabled` | `false` | 白名单写入 Patrol Actuator |
| `migrate.enabled` | `true` | 运行串行化的 Migration initContainer |

`values.schema.json` 会验证执行内核契约并拒绝已淘汰的部署键。

## 必填密钥

必须覆盖所有占位值，尤其以下值必须互不相同：

- `secrets.postgresAdminPassword`
- `secrets.postgresMigrationPassword`
- `secrets.postgresPassword`
- `secrets.executionKernelPostgresPassword`
- `secrets.redisPassword`
- `secrets.apiKeySecret`、`secrets.auditSigningKey`、`secrets.jwtSecret`、
  `secrets.sessionSecret`
- `secrets.bootstrapAdminPassword`

使用批准的 Secret Manager，不要提交生产 Values 文件。PostgreSQL 管理员凭证仅用于
Bootstrap，不会注入 API 或执行内核容器。

## 安全要求

- 保持 `networkPolicy.enabled=true`，沙箱 Ingress 只允许 API/执行内核。
- Collector 保持只读；Actuator 独立部署并使用明确白名单。
- 配置公网 HTTPS 前端/OAuth URL 与 `env.COOKIE_SECURE=true`。
- 精确设置可信 Proxy CIDR、出站端口与私网主机白名单。
- 任何运行时数据库角色都不得拥有 `SUPERUSER` 或 `BYPASSRLS`。
- 只部署到全新数据库；本 Chart 不包含旧 Catalog 转换路径。

Chart 托管 PostgreSQL 只在全新数据库初始化时运行 `files/postgres/init-app-role.sh`，
在 Alembic 之前创建互相独立的 Migration、API 与 Kernel 角色。外部全新数据库必须先
配置等价角色，并执行以下查询验证：

```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('opencitadel_app', 'opencitadel_execution_kernel_runtime');
```

## 扩缩容与验证

```bash
kubectl -n opencitadel rollout status deployment/opencitadel-api
kubectl -n opencitadel rollout status deployment/opencitadel-execution-kernel
kubectl -n opencitadel scale deployment/opencitadel-execution-kernel --replicas=4

helm template opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --values values.production.yaml >/dev/null
kubectl -n opencitadel get networkpolicy
```

Release Tag 发布
`ghcr.io/ocealong/opencitadel-{api,execution-kernel,migrate,ui,sandbox,ops-collector,ops-actuator}`。

参见[部署指南](../../../docs/operations/deployment.zh-CN.md)、
[执行内核架构](../../../docs/architecture/execution-kernel.zh-CN.md)和
[Ops Patrol 运维](../../../docs/operations/ops-patrol.zh-CN.md)。
