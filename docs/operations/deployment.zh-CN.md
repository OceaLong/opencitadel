# 部署指南

[English](deployment.md)

OpenCitadel 只部署一个无状态 API 与一个数据库权威执行内核。PostgreSQL 是必需组件；
Redis 只降低唤醒延迟。当前 schema 是单 revision 的全新设计，应部署到新数据库，不导入
早期开发 catalog。

## 进程

| 进程 | Compose 服务 | 数据库凭据 |
| --- | --- | --- |
| Migration | `opencitadel-migrate` | `POSTGRES_MIGRATION_*` |
| API | `opencitadel-api` | `POSTGRES_USER` / `POSTGRES_PASSWORD` |
| 执行内核 | `opencitadel-execution-kernel` | `POSTGRES_KERNEL_*` |
| UI | `opencitadel-ui` | 无 |

PostgreSQL 管理员凭据只用于初始化，运行时容器不得接收。执行内核运行 Command Inbox、
Run 决策、Activity、Timer、Outbox、正式投影、自动化与维护 Tick；不存在第二执行服务。

每个角色只加载一次部署配置，并且只构建自己的手工强类型对象图：API 持有 `ApiRuntime`，
执行进程持有 `KernelRuntime`。两者的 `TaskSupervisor` 以及 PostgreSQL、Redis、对象存储、
Provider 和连接池资源完全独立。

## Compose 快速启动

```bash
cp .env.example .env
# 替换 .env 中全部必填 Secret 与密码。
docker compose --profile local up -d --build
docker compose ps
```

打开 `http://localhost:8088`。`local` profile 启用内置 MinIO。云部署可设置
`STORAGE_PROVIDER=cos` 与 `COS_*` 使用 COS。

至少为以下变量设置强且互不相同的值：

- `POSTGRES_ADMIN_USER`、`POSTGRES_ADMIN_PASSWORD`、
  `POSTGRES_MIGRATION_USER`、`POSTGRES_MIGRATION_PASSWORD`、
  `POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_KERNEL_USER`、
  `POSTGRES_KERNEL_PASSWORD`
- `REDIS_PASSWORD`、`BOOTSTRAP_ADMIN_PASSWORD`
- `API_KEY_SECRET_ID`、`API_KEY_SECRET`、`API_KEY_PREVIOUS_SECRETS`
- `AUDIT_SIGNING_KEY_ID`、`AUDIT_SIGNING_KEY`、`AUDIT_PREVIOUS_SIGNING_KEYS`
- `JWT_SECRET`、`SESSION_SECRET`
- `SANDBOX_BROKER_TOKEN`、`SANDBOX_TOKEN_SEED`
- `OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS`

`SANDBOX_TOKEN_SEED` 在生产环境必填且至少 32 个随机字节；API 与执行内核都由它派生每个沙箱的
数据面 Token。`JWT_PREVIOUS_SECRETS`（默认 `{}`）与 `DATABASE_AUTHORIZATION_SIGNING_SECRET`
（默认复用 `SESSION_SECRET`）为可选，详见*配置与 Secret*。运行 Ops Patrol Collector/Actuator
时，还需设置强 `OPS_COLLECTOR_TOKEN` 与 `OPS_ACTUATOR_TOKEN`；缺失时对应 Server 拒绝启动。

密码学密钥至少使用 32 个随机字节。除本地 HTTP 开发外保持 `COOKIE_SECURE=true`，将
`FRONTEND_BASE_URL` 与 `OAUTH_REDIRECT_BASE` 设置为公网 HTTPS Origin，精确配置
`TRUSTED_PROXY_CIDRS`，并收紧 `OUTBOUND_ALLOWED_PORTS` 与
`OUTBOUND_PRIVATE_HOST_ALLOWLIST`。生产环境 `TRUSTED_PROXY_CIDRS` 在启动时校验，拒绝与
沙箱/Pod 网络重叠的宽 RFC1918 段。

## 启动与恢复

Compose 启动 PostgreSQL/Redis，执行一次性 migration，再启动 API、执行内核、UI 与代理。
API 遇到未到 Alembic head 的 schema 会拒绝启动。

```bash
docker compose logs -f opencitadel-migrate
docker compose logs -f opencitadel-api
docker compose logs -f opencitadel-execution-kernel
```

执行内核可安全重启或扩容：Claim 使用数据库 fencing，待处理工作从 PostgreSQL 回收。
Redis 可被清空或重启；没有提示时内核会轮询 pending 行。不要把 Redis Key 当作备份数据。

## 健康探针与有界排空

API 暴露两个无需认证且语义分离的进程探针：

- `/api/health/live`：HTTP 进程仍能提供服务时成功；
- `/api/health/ready`：完整 `ApiRuntime` 构建完成后才成功，并在排空归属任务前变为不可用。

`/api/status` 继续作为依赖诊断，不作为 Kubernetes Liveness Probe。Kernel 使用
`python -m app.execution_kernel_health readiness|liveness`；归属明确的 Heartbeat 原子写入
Marker，并在关闭时删除。Readiness 还会校验 Runtime Policy、Schema 与专用 Kernel 数据库角色。

通过 `OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS=30` 配置应用有界排空。Compose 使用
`stop_grace_period: 45s`；Helm 使用 `shutdown.timeoutSeconds: 30` 与
`shutdown.terminationGracePeriodSeconds: 45`。平台终止宽限必须始终大于应用超时。

## 存储与沙箱

生产对象存储必须被所有 API 与内核副本共享。使用 COS 或 S3 兼容 MinIO 私有 Bucket；
本地文件系统不适合多副本。

Compose 将 Docker 访问隔离在 `opencitadel-sandbox-broker`。API 和内核只拿到窄化、
Token 认证的 HTTP 端点，不接触 Docker Socket。原生 Linux 需把 `DOCKER_SOCK_GID`
设为 Socket Group。Kubernetes 使用执行内核专用 ServiceAccount 与受限 Sandbox Pod RBAC。
保持 Squid 沙箱 Egress Proxy 与 Allowlist 开启。每个沙箱的数据面 Token 在 API 与内核两侧派生为
`HMAC(SANDBOX_TOKEN_SEED, sandbox_id)`；Seed 绝不进入沙箱容器，任何副本都能无共享 Token 状态
地重新附着并认证。

## 配置与 Secret

Migration 在空库中 Seed 类型化 Execution/Operations Policy Revision 及其原子 Head。
Admin 通过 **设置 → 运行时策略** 或 `/api/runtime-policies` 管理后续不可变 Revision。
环境变量只承载部署拓扑、身份、Credential、Endpoint 与 Bootstrap，不覆盖 Policy Field。

推理 Endpoint 与集成凭据只存为版本化 `fernet_v2` 信封。密钥轮换步骤：

1. 按旧 Key ID 把旧密钥加入 `API_KEY_PREVIOUS_SECRETS`。
2. 设置新的 `API_KEY_SECRET_ID` 与 `API_KEY_SECRET`。
3. 重启 API 与执行内核副本。
4. 轮换 Provider 凭据并保存受影响的 Endpoint/Integration；新写入使用当前 Key。
5. 确认没有存量信封使用旧 ID 后，再删除旧 Key。

审计签名密钥通过 `AUDIT_PREVIOUS_SIGNING_KEYS` 同样轮换，Session JWT 通过
`JWT_PREVIOUS_SECRETS` 轮换：把旧 Key 按其 ID 移入 previous map，设置新的 `JWT_SECRET`，
再重启副本；在途 Token 在过期前继续验证。`DATABASE_AUTHORIZATION_SIGNING_SECRET` 默认回退
`SESSION_SECRET`，保持现有部署与其 Seed 的 RLS `app.rls_signing_secret` 值不变；仅当需要把
数据库授权信任域与 Session Cookie 拆分时才设置为独立强值，并与数据库签名密钥同步轮换。不得
记录明文 Secret，也不得把它们写进 Runtime Policy。

Bootstrap 后，通过 **设置 → 推理** 或 `/api/inference` 配置 Endpoint、类型化 Model 与用途
Binding。Chat、Embedding、Rerank 消费者不存在环境变量 Key 回退；Binding 无法解析时通过
`/api/capabilities` 报告并 Fail Closed。可选 `DEMO_INFERENCE_*` 变量只供显式 Demo Seed
命令使用。

## 可观测性

设置 `METRICS_TOKEN` 后开放需认证的 API 指标。设置
`EXECUTION_KERNEL_METRICS_PORT`（默认 `9108`）开放内网 Kernel Prometheus 端点，
并用网络策略限制抓取方。重点监控：

- Command、Activity、Timer、Outbox 的 pending 数量与最老年龄；
- Activity Claim 过期、未知结果、重试和审批等待；
- Projector Lag 与哈希/完整性失败；
- PostgreSQL 连接、容量、锁与强制 RLS 错误；
- 沙箱配额、Provider 延迟与对象存储失败。

完整性或 OwnerScope 错误会关闭失败，必须调查；不得修改事件行绕过。

## Helm

Chart 位于 `deploy/helm/opencitadel`。

```bash
helm lint deploy/helm/opencitadel
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml
```

通过 Secret Manager 或受保护 values 文件提供全部 Secret。保持
`networkPolicy.enabled=true`，分离 API/Kernel/Migration 数据库用户，并按 Activity 负载
配置 `executionKernel.replicas` 与 HPA。可选 Ops Collector 与 Actuator 必须网络隔离；
Actuator 只允许 API/Kernel 到达，且仍要求持久审批。其 RBAC 是按允许 Namespace 渲染的
Namespaced `Role`/`RoleBinding`，而非集群级 `ClusterRole`。

Chart 以模板形式提供韧性与可观测基线：按工作负载的 `NetworkPolicy`（PostgreSQL、Redis、
执行内核、Ops Collector/Actuator、Egress Proxy、Sandbox）、API 与执行内核的
PodDisruptionBudget、将沙箱出站限制到 Allowlist 的 Squid `egress-proxy` Deployment、
PostgreSQL 备份 `CronJob`，以及带审批超时、审计链验证失败、Outbox Lag、沙箱准入拒绝、5xx
率、限流拒绝告警的 `PrometheusRule`。Reverse Proxy 还设置 HSTS/CSP/nosniff 响应头与
`server_tokens off`。

Chart 托管 PostgreSQL 时，`files/postgres/init-app-role.sh` 会在绿地迁移前创建互相独立的
Migration、API 与 Kernel 角色。外部数据库必须在安装前配置等价角色。验证运行时角色的
`rolsuper=false` 且 `rolbypassrls=false`；API 与 Kernel 容器不得拥有 Schema 或 Migration
凭据。

## 发布产物与供应链

Release Tag 发布七个镜像：`api`、`execution-kernel`、`migrate`、`ui`、`sandbox`、
`ops-collector` 和 `ops-actuator`。`.github/workflows/security.yml` 执行 Gitleaks、CodeQL
与 Trivy；Release Workflow 在发布前扫描每个镜像，并附加 SBOM 与签名 provenance。
部署时应验证 provenance 并使用不可变 Digest，不依赖 `latest`。

`e2e/fixtures/` 下的确定性推理 Provider 不是发布产物。它只存在于 Compose
`acceptance` Profile，禁止加入 Helm、Kustomize、Quickstart、生产设置或 Release
镜像矩阵。

## 确定性验收门禁

运行与 CI 相同的发布阻断全栈门禁：

```bash
./scripts/run-acceptance-e2e.sh --disposable
```

Runner 持有唯一 Compose Project 与 Run Namespace，通过公共控制面配置推理，执行真实
Execution Kernel 与 Collector，并写入 `tmp/acceptance/<run-id>/manifest.json`。证据
Schema 为 `contracts/acceptance-evidence.schema.json`；任一必需 ID 缺失、重复、跳过、
中断或失败都会使门禁失败。

清理严格绑定 `com.docker.compose.project`、`com.opencitadel.acceptance.project` 与
`com.opencitadel.acceptance.run` Label。动态 Sandbox 还必须带有
`opencitadel.io/sandbox=true` 和 Run Scope 名称前缀。带 `--disposable` 时，本次运行
归属的 Volume 必须归零；不带时保留并报告 Volume 与产品历史，但 Container、Network
和动态 Sandbox 仍须排空。

无论门禁成功或失败，CI 都把 `tmp/acceptance/` 发布为 `acceptance-evidence` Artifact。
重试前检查 Manifest 的 `failure_reason`、`logs/stack.log` 和 Playwright Trace/截图。
不得用宽泛 Docker Prune 替代 Runner 清理。

## 发布门禁

```bash
cd api
uv run pytest -q
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
helm lint deploy/helm/opencitadel
./scripts/run-acceptance-e2e.sh --disposable
```

数据库执行/RLS 测试需要一次性 PostgreSQL，覆盖追加式事件、Owner 隔离、角色授权、
Inbox 幂等、Timer/Outbox 恢复、Snapshot 与 Projector Rebuild。
