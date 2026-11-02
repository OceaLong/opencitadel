[English](README.md)

# OpenCitadel Helm Chart

本 Chart 部署全新的 OpenCitadel 运行时：API、统一执行内核、UI、沙箱接入、
PostgreSQL/Redis 选项，以及可选的 Ops Collector/Actuator。

## 前置条件

- Kubernetes 1.24+
- Helm 3.x
- `opencitadel-api`、`opencitadel-execution-kernel`、`opencitadel-ui`、
  `opencitadel-sandbox` 镜像
- 内置有状态服务需要持久卷供应，或使用外部 PostgreSQL（pgvector）、Redis 与对象存储

Chart 可为自包含安装创建 PostgreSQL、Redis 与 MinIO。内置 PostgreSQL 与 Redis 均为
单副本、仅评估级；生产请使用外部/托管服务或 CloudNativePG 等 Operator，并设置
`postgresql.enabled=false` / `redis.enabled=false`，同时用 `env.POSTGRES_HOST`、
`env.REDIS_HOST`（及 `secrets.*` 中的相关凭证）指向它。

## 安装

自包含安装只需提供公开 HTTPS Origin：

```bash
helm lint deploy/helm/opencitadel --set publicUrl=https://citadel.example.com
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --set publicUrl=https://citadel.example.com
```

PostgreSQL、Redis 与 MinIO 默认启用。服务地址、数据库名称/用户名、沙箱 Namespace 与
出站代理地址从 Chart Values 和 Release 推导。镜像默认使用 GHCR 发布仓库及 Chart 版本。
首次安装在按角色划分的 Secret 中生成互相独立的随机凭据，升级复用原值。NOTES 提供通过
`kubectl` 读取初始管理员邮箱/密码的命令；备份、恢复持久数据时保留这些 Secret。

Migration 在新数据库中初始化基础知识库处理：文本提取与关键词检索，向量检索、重排、
GraphRAG 和 OCR 关闭。在 **设置 → 推理** 添加 Chat Binding 即可生成 Ask/Agent 回答；
可选模型与运行时策略开关步骤见
[知识库教程](../../../docs/tutorials/02-internal-knowledge-base.zh-CN.md)。升级保留已有策略。

Ingress 默认关闭：将公开 Origin 的 `/api` 路由到 API Service、`/` 路由到 UI，或开启
`ingress.enabled` 并提供 Controller 的 Class/TLS 配置。未设置 `publicUrl` 时，已启用
Ingress 的明确 `ingress.host` 可作为 Origin 来源。生产渲染拒绝缺失或非 HTTPS 的
Origin；Chart 无法申请证书或推断真实域名。本地 Chart 体验显式设置
`--set env.ENV=development`，并通过端口转发或已有路由访问服务。

原有 `env.FRONTEND_BASE_URL`、OAuth 与 Cookie 覆盖继续有效；旧的前端 Origin 覆盖优先于 `publicUrl`。
未显式设置 `env.STORAGE_PROVIDER` 时，已有完整 `env.COS_REGION` / `env.COS_BUCKET`
配置继续选择 COS；不需要内置 MinIO 时关闭 `minio.enabled`。
COS 与外部服务仍作为可选覆盖。分类完整参考见 `values.yaml` / `values.schema.json`。
内置有状态服务简化启动；生产可用性和备份仍取决于所选存储与服务。

## 运行拓扑

| Workload       | 职责                                                    | PostgreSQL 角色                |
| -------------- | ------------------------------------------------------- | ------------------------------ |
| API            | HTTP、认证、准入、公开 SSE                              | `postgresql.user`              |
| Migration init | 绿地 Alembic 迁移链升级至当前 Head 与首次配置 Seed      | `postgresql.migrationUser`     |
| 执行内核       | Command、决策、Activity、Timer、Outbox、投影、Scheduler | `executionKernel.databaseUser` |
| UI             | Next.js 应用                                            | 无                             |

每个迁移调用都在 Schema Upgrade 与首次 Seed 的完整区间持有同一个 PostgreSQL
Advisory Lock，因此多个 API initContainer 会自动串行。API 与执行内核凭证不能迁移
Schema；内核只有 Append、Claim 与 Projection 所需的运行权限。

## 主要 Values

| 参数                                     | 默认值                                 | 说明                                                  |
| ---------------------------------------- | -------------------------------------- | ----------------------------------------------------- |
| `replicaCount.api`                       | `2`                                    | API 副本数                                            |
| `executionKernel.replicas`               | `2`                                    | 执行内核副本数                                        |
| `executionKernel.databaseUser`           | `opencitadel_execution_kernel_runtime` | 专用内核角色                                          |
| `executionKernel.metricsPort`            | `9108`                                 | 集群内 Prometheus 端口                                |
| `shutdown.timeoutSeconds`                | `90`                                   | 应用任务有界排空时间                                  |
| `shutdown.terminationGracePeriodSeconds` | `120`                                   | Pod 宽限期，必须大于排空时间                          |
| `autoscaling.api.enabled`                | `true`                                 | API HPA                                               |
| `autoscaling.executionKernel.enabled`    | `true`                                 | 执行内核 HPA                                          |
| `postgresql.enabled`                     | `true`                                 | Chart 托管的全新 PostgreSQL                           |
| `redis.enabled`                          | `true`                                 | Chart 托管 Redis                                      |
| `minio.enabled`                          | `true`                                | Chart 托管 MinIO                               |
| `networkPolicy.enabled`                  | `true`                                 | Workload 网络隔离                                     |
| `egressProxy.enabled`                    | `true`                                 | 沙箱出站代理（squid），沙箱 NetworkPolicy 已依赖它    |
| `pdb.enabled`                            | `false`                                | 为 api/内核配置 PodDisruptionBudget（minAvailable:1） |
| `topologySpread.enabled`                 | `true`                                 | 将 api/内核副本跨节点分散                             |
| `monitoring.prometheusRule.enabled`      | `false`                                | 渲染基线 PrometheusRule 告警                          |
| `backup.enabled`                         | `false`                                | 定时 pg_dump 备份 CronJob（写入 PVC）                 |
| `opsCollector.enabled`                   | `false`                                | 固定只读 Patrol Collector                             |
| `opsActuator.enabled`                    | `false`                                | 白名单写操作与能力发现 Patrol Actuator                |
| `migrate.enabled`                        | `true`                                 | 运行串行化的 Migration initContainer                  |

`values.schema.json` 会验证执行内核契约并拒绝已淘汰的部署键。

## 韧性与可观测

- `pdb.enabled=true` 在自愿中断（滚动更新/节点排空）期间为 api/内核至少保留一个可用
  Pod；仅在多副本下使用。
- `topologySpread.enabled`（默认开启）以软约束（`ScheduleAnyway`）按主机名将 api/内核
  跨节点分散，单节点集群仍可调度。
- `monitoring.prometheusRule.enabled=true` 渲染 `PrometheusRule`（需 Prometheus
  Operator），包含基线告警：审批决策超时率、审计链验证失败、执行 outbox 投递滞后/重投
  堆积、沙箱准入拒绝率、HTTP 5xx 率、限流拒绝率。API 已实现
  `http_requests_total` / `rate_limit_rejected_total`；必须实际抓取才会有数据。
- `backup.enabled=true` 且 `postgresql.enabled=true` 才渲染 `<release>-postgres-backup` CronJob/PVC，按份数保留 `pg_dump`。此模板不提供 HTTP 状态端点；`opsCollector.registeredBackups` 还需独立提供 `status_url`，不能填 CronJob 名称代替 URL。PVC 转储仅覆盖数据库、仅评估级；生产应采用一致的数据库/对象存储备份。

启用 `monitoring.serviceMonitor.enabled=true` 后，使用自动生成的强指标 Token（或显式 `secrets.metricsToken` 覆盖）通过 Bearer 认证抓取 `/api/metrics`；空 Token 返回 404。ServiceMonitor 当前只抓取 API；内核的 9108 指标 Service 需要另外配置 Prometheus 抓取。所有监控 CRD、Selector 和内核指标 Egress/Ingress 都需与实际 Prometheus 部署匹配。

## 持久密钥与覆盖

空值在首次安装时生成凭据，升级时复用已有 Secret，包括数据库各角色、Redis、MinIO、
`secrets.apiKeySecret`、`secrets.auditSigningKey`、`secrets.jwtSecret`、
`secrets.sessionSecret`、`secrets.databaseAuthorizationSigningSecret`、
`secrets.publicCursorSecret`、`secrets.bootstrapAdminPassword`、
`secrets.sandboxTokenSeed`，以及启用 ServiceMonitor 时的指标凭据。首次启用
Collector/Actuator 时生成各自 Token。
显式 `secrets.*`、`opsCollector.token` / `opsActuator.token` 覆盖继续优先。

使用 Secret Manager 时，将 `existingSecrets.application`、`postgresBootstrap`、
`migration`、`executionKernel`、`opsCollector` 或 `opsActuator` 设置为提前创建的 Secret
名称，Key 使用对应生成模板中的环境变量名。Chart 不再生成选中的 Secret 资源。
保留角色隔离：PostgreSQL 管理员凭据只属于 Bootstrap 与可选备份，Migration 凭据不得
进入运行时 API/Kernel 容器。

升级时若预期 Secret 或必需 Key 缺失，会拒绝生成替代密钥。旧 Release 没有独立数据库签名
Key 时，继续保留原 `SESSION_SECRET` 回退。因此无集群访问的 `helm template --is-upgrade`
需要显式凭据；实际升级通过 Kubernetes Lookup 读取已有值。首次安装渲染清单包含 Secret，
应保护输出文件且不得提交。使用 Secret Manager 的轮换流程，并按
[部署指南](../../../docs/operations/deployment.zh-CN.md)保留加密/签名 Key Ring。

## 安全要求

- 保持 `networkPolicy.enabled=true`，沙箱 Ingress 只允许 API/执行内核。
- 保持 `egressProxy.enabled=true`：它部署所有沙箱出站必经的 squid 代理。沙箱
  NetworkPolicy 仅放行 DNS 与到代理 Pod（label
  `app.kubernetes.io/component=egress-proxy`）的 3128，由代理解析目标并执行
  `deploy/squid/squid.conf` 的私网/元数据黑名单。api/内核的
  `SANDBOX_HTTP_PROXY`、`SANDBOX_HTTPS_PROXY`、`SANDBOX_CHROME_ARGS` 默认指向
  `http://<release>-egress-proxy:3128`。关闭它会使沙箱除 DNS 外全部 fail-closed，
  如需外部代理，除覆盖 `env.SANDBOX_*` 地址外，还必须让 NetworkPolicy 允许该精确代理目标。当前 Squid 配置仅使用地址黑名单与 Safe_ports；`egressProxy.allowedDomains` 预留且未生效。
- Collector 保持只读；Actuator 独立部署并使用明确白名单。
- 提供真实 HTTPS `publicUrl`；OAuth 与安全 Cookie 默认值从它推导。
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

专用 Migration 登录角色还需要应用数据库上的 `CREATE` 权限，以在临时校验 schema 中核对固定 DDL。该授权不包含 `CREATEDB`、`SUPERUSER`、`BYPASSRLS` 或数据库权限转授权；API 和 Kernel 登录角色不得获得数据库 `CREATE`。已有部署需要运维人员明确审核并更新角色配置后再运行迁移，应用启动不会自动提权。

普通扩容应修改 `autoscaling.executionKernel.minReplicas/maxReplicas` 或副本 Values；默认 HPA 开启，手工 `kubectl scale` 会被 HPA 后续协调覆盖。API/内核 Readiness 和 Liveness 使用独立进程探针；Collector/Actuator 是 TCP 探针，只证明监听可达。执行内核包含四个关键评测循环，异常会撤下 Readiness；默认不启用本地受控物理环境。完整 AC21 容量验收仍需实测证据，目前尚未完成。

## 扩缩容与验证

```bash
kubectl -n opencitadel rollout status deployment/opencitadel-api
kubectl -n opencitadel rollout status deployment/opencitadel-execution-kernel
kubectl -n opencitadel scale deployment/opencitadel-execution-kernel --replicas=4

helm template opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --set publicUrl=https://citadel.example.com >/dev/null
kubectl -n opencitadel get networkpolicy
```

Release Tag 发布
`ghcr.io/ocealong/opencitadel-{api,execution-kernel,migrate,ui,sandbox,ops-collector,ops-actuator}`。

参见[部署指南](../../../docs/operations/deployment.zh-CN.md)、
[执行内核架构](../../../docs/architecture/execution-kernel.zh-CN.md)和
[Ops Patrol 运维](../../../docs/operations/ops-patrol.zh-CN.md)。

交付物上传清理意图使用独立的运行时 PostgreSQL 连接池（每进程最多两个连接，
不允许溢出），沿用当前应用/kernel 数据库角色及签名请求授权。连接获取上限为
10 秒，单次意图写入总期限为 10 秒；运行时关闭时统一释放。部署时须在普通
连接池之外计入这两个连接，避免持有交付物锁时等待同一连接池的第二条连接。
