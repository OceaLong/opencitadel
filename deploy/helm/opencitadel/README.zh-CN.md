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

Chart 可为自包含安装创建 PostgreSQL、Redis 与 MinIO。内置 PostgreSQL 与 Redis 均为
单副本、仅评估级；生产请使用外部/托管服务或 CloudNativePG 等 Operator，并设置
`postgresql.enabled=false` / `redis.enabled=false`，同时用 `env.POSTGRES_HOST`、
`env.REDIS_HOST`（及 `secrets.*` 中的相关凭证）指向它。

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
| `egressProxy.enabled` | `true` | 沙箱出站代理（squid），沙箱 NetworkPolicy 已依赖它 |
| `pdb.enabled` | `false` | 为 api/内核配置 PodDisruptionBudget（minAvailable:1） |
| `topologySpread.enabled` | `true` | 将 api/内核副本跨节点分散 |
| `monitoring.prometheusRule.enabled` | `false` | 渲染基线 PrometheusRule 告警 |
| `backup.enabled` | `false` | 定时 pg_dump 备份 CronJob（写入 PVC） |
| `opsCollector.enabled` | `false` | 固定只读 Patrol Collector |
| `opsActuator.enabled` | `false` | 白名单写入 Patrol Actuator |
| `migrate.enabled` | `true` | 运行串行化的 Migration initContainer |

`values.schema.json` 会验证执行内核契约并拒绝已淘汰的部署键。

## 韧性与可观测

- `pdb.enabled=true` 在自愿中断（滚动更新/节点排空）期间为 api/内核至少保留一个可用
  Pod；仅在多副本下使用。
- `topologySpread.enabled`（默认开启）以软约束（`ScheduleAnyway`）按主机名将 api/内核
  跨节点分散，单节点集群仍可调度。
- `monitoring.prometheusRule.enabled=true` 渲染 `PrometheusRule`（需 Prometheus
  Operator），包含基线告警：审批决策超时率、审计链验证失败、执行 outbox 投递滞后/重投
  堆积、沙箱准入拒绝率、HTTP 5xx 率、限流拒绝率。后两项依赖 API 落地
  `http_requests_total` / `rate_limit_rejected_total`；在此之前这两条规则不会有数据。
- `backup.enabled=true` 运行定时 `pg_dump` CronJob，写入独立 PVC 并按份数保留。其资源名
  `opencitadel-postgres-backup` 即 Ops Patrol `opsCollector.registeredBackups` 条目的
  对应目标，使巡检备份项有真实来源。该本地 PVC 转储仅评估级；生产应通过托管数据库备份
  或将转储投递到对象存储。

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
- 保持 `egressProxy.enabled=true`：它部署所有沙箱出站必经的 squid 代理。沙箱
  NetworkPolicy 仅放行 DNS 与到代理 Pod（label
  `app.kubernetes.io/component=egress-proxy`）的 3128，由代理解析目标并执行
  `deploy/squid/squid.conf` 的私网/元数据黑名单。api/内核的
  `SANDBOX_HTTP_PROXY`、`SANDBOX_HTTPS_PROXY`、`SANDBOX_CHROME_ARGS` 默认指向
  `http://<release>-egress-proxy:3128`。关闭它会使沙箱除 DNS 外全部 fail-closed，
  除非同时放开沙箱 NetworkPolicy；如需外部代理，请把上述 `env.SANDBOX_*` 覆盖为
  该代理地址。
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
