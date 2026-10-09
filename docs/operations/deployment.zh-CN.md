# 部署指南

[English](deployment.md)

OpenCitadel 只部署一个无状态 API 与一个数据库权威执行内核。PostgreSQL 是必需组件；
Redis 只降低唤醒延迟。当前 Schema 使用从 `0001greenfield` 到
`0030evaluation_judge_history` 的单一绿地迁移链，包含执行、分析和评测的增量迁移。
应部署到新数据库，由 `app.migrate` 升级至当前 Head，不导入早期开发 Catalog。

## 进程

| 进程      | Compose 服务                   | 数据库凭据                            |
| --------- | ------------------------------ | ------------------------------------- |
| Migration | `opencitadel-migrate`          | `POSTGRES_MIGRATION_*`                |
| API       | `opencitadel-api`              | `POSTGRES_USER` / `POSTGRES_PASSWORD` |
| 执行内核  | `opencitadel-execution-kernel` | `POSTGRES_KERNEL_*`                   |
| UI        | `opencitadel-ui`               | 无                                    |

PostgreSQL 管理员凭据用于初始化及可选的 Helm 数据库备份 Job；API 与执行内核运行时容器不得接收。执行内核运行 Command Inbox、
Run 决策、Activity、Timer、Outbox、正式投影、自动化与维护 Tick；不存在第二执行服务。

每个角色只加载一次部署配置，并且只构建自己的手工强类型对象图：API 持有 `ApiRuntime`，
执行进程持有 `KernelRuntime`。两者的 `TaskSupervisor` 以及 PostgreSQL、Redis、对象存储、
Provider 和连接池资源完全独立。

## Compose 快速启动

```bash
cp .env.example .env
# 替换 .env 中全部必填 Secret 与密码；本地 HTTP 设置 COOKIE_SECURE=false。
# 设置 FRONTEND_BASE_URL=http://localhost:8088、OAUTH_REDIRECT_BASE=http://localhost:8088/api/auth/oauth。
# 将 OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS 设为 30，与 Compose 45s 宽限期协调。
docker compose build opencitadel-sandbox
docker compose --profile local up -d --build
docker compose ps
```

打开 `http://localhost:8088`。`local` profile 启用内置 MinIO。云部署可设置
`STORAGE_PROVIDER=cos` 与 `COS_*` 使用 COS。

配置以下部署项；密码与密钥使用独立强值，ID/用户名/超时不作为密钥：

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
保持 Squid 沙箱 Egress Proxy 开启；当前静态配置使用私网/元数据黑名单与 Safe_ports，并没有域名 Allowlist。每个沙箱的数据面 Token 在 API 与内核两侧派生为
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
数据库授权信任域与 Session Cookie 拆分时才设置为独立强值。数据库在首次迁移时记录该 Secret；已有数据库的值发生变化时，先执行 `python -m app.rotate_db_signing_secret`（可重复执行，同一事务内更新并校验签名探针），再重启 API 与执行内核。不得
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
helm lint deploy/helm/opencitadel --values values.production.yaml
helm upgrade --install opencitadel deploy/helm/opencitadel \
  --namespace opencitadel --create-namespace \
  --values values.production.yaml
```

通过 Secret Manager 或受保护 values 文件提供全部 Secret。保持
`networkPolicy.enabled=true`，分离 API/Kernel/Migration 数据库用户，并按 Activity 负载
配置 `executionKernel.replicas` 与 HPA，并把 `env.SANDBOX_K8S_NAMESPACE` 设为 Release Namespace（此例为 `opencitadel`），与沙箱 Role 和 NetworkPolicy 对齐。可选 Ops Collector 与 Actuator 必须网络隔离；
Actuator 只允许 API/Kernel 到达，且仍要求持久审批。其 RBAC 是按允许 Namespace 渲染的
Namespaced `Role`/`RoleBinding`，而非集群级 `ClusterRole`。

Chart 提供这些模板，但并非全部默认开启：`networkPolicy.enabled=true` 与 `egressProxy.enabled=true` 默认启用；`pdb.enabled`、`backup.enabled`、`monitoring.serviceMonitor.enabled`、`monitoring.prometheusRule.enabled` 默认关闭。备份 CronJob 还要求 Chart 托管 PostgreSQL。Squid 仅使用当前静态地址/端口 ACL，`egressProxy.allowedDomains` 尚未消费。Prometheus Operator CRD、Selector 与实际抓取必须配置后才有告警数据；API 抓取还需强 `secrets.metricsToken`。Compose Nginx 模板设置 CSP/nosniff 与 HTTPS HSTS；Helm 只在启用 Ingress 时通过 ingress-nginx Annotation 提供安全响应头，Controller 必须允许该配置。

Chart 托管 PostgreSQL 时，`files/postgres/init-app-role.sh` 会在绿地迁移前创建互相独立的
Migration、API 与 Kernel 角色。外部数据库必须在安装前配置等价角色。验证运行时角色的
`rolsuper=false` 且 `rolbypassrls=false`；API 与 Kernel 容器不得拥有 Schema 或 Migration
凭据。

## 发布产物与供应链

Release Tag 发布八个镜像：`api`、`execution-kernel`、`migrate`、`sandbox-broker`、
`ui`、`sandbox`、`ops-collector` 和 `ops-actuator`。`.github/workflows/security.yml` 执行 Gitleaks、CodeQL
与 Trivy；Release Workflow 在发布前扫描每个镜像，并附加 SBOM 与签名 provenance。
部署时应验证 provenance 并使用不可变 Digest，不依赖 `latest`。

`e2e/fixtures/` 下的确定性推理 Provider 不是发布产物。它只存在于 Compose
`acceptance` Profile，禁止加入 Helm、Kustomize、Quickstart、生产设置或 Release
镜像矩阵。

## 评测运行时部署

评测由同一个执行内核持有四个关键循环：`evaluation-scheduler`、`evaluation-reconciler`、`evaluation-scoring`、`evaluation-cleanup`；API 只做授权、校验与持久化准入。循环异常会撤下内核 Readiness 并请求关闭。录制/隔离 Subject 与 Judge 都提交正式 Run，没有独立执行服务。默认部署不启用受控物理环境 Adapter；本地 Docker Adapter 需非生产 `ENV`、`EVALUATION_LOCAL_DOCKER_ENABLED=true` 与只读管理员清单 `EVALUATION_TEST_INVENTORY_PATH`，并配置物理预算库存。验收 Profile 的清单与 Broker Journal 卷由专用覆盖文件注入，不应复制到生产。详见[评测控制面](../architecture/evaluation-control-plane.zh-CN.md)与[受控评测环境](../evaluation-environments.md)。

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

完整 Execution 验收还要求 `ACCEPTANCE_CAPACITY_REPORT` 与 `ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST` 提供并通过 AC21 实测证据校验；只跑 UI/单元测试不代表容量通过。AC21 全容量验收目前尚未完成，不应把部署成功或其他用例通过描述为全部门禁通过。

## 发布门禁

下列 API 命令只排除 `test_execution_visualization_closed_loop.py` 的六项当次验收消费者。
验收 Runner 在原生 strict 报告与恢复回执校验后执行这些断言，保留 `strict-pytest.xml`，
要求六项全部通过且零跳过。

```bash
cd api
uv run pytest -q --ignore=tests/app/integration/test_execution_visualization_closed_loop.py
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
helm lint deploy/helm/opencitadel --values values.production.yaml
./scripts/run-acceptance-e2e.sh --disposable
```

数据库执行/RLS 测试需要一次性 PostgreSQL，覆盖追加式事件、Owner 隔离、角色授权、
Inbox 幂等、Timer/Outbox 恢复、Snapshot 与 Projector Rebuild。

## 本地 Compose 备份与隔离恢复

在仓库根目录运行，并使用启动当前服务时的 `.env`、`COMPOSE_FILE` 覆盖文件、
Profile 和 `COMPOSE_PROJECT_NAME`：

```bash
bash scripts/backup.sh backups/2026-09-07
bash scripts/verify-backup.sh backups/2026-09-07
bash scripts/restore.sh backups/2026-09-07 restore_drill_20260907
```

备份目标目录必须不存在。脚本在内存中读取已解析的 Compose 配置，确定数据库、
管理员用户名和实际命名 MinIO 卷，不保存完整配置或密钥。备份期间停止运行中的
应用服务和 MinIO，然后采集数据库与对象快照；成功、失败或 SIGTERM 都通过
`finally` 恢复先前运行的服务。请安排维护窗口，API、内核及其他 Compose 应用服务
在此期间不可用，PostgreSQL 与 Redis 保持运行。必须先停止项目外的写入方；脚本
无法隔离外部写入。SIGKILL 或主机故障无法执行清理，此时须检查源服务并按维护
记录启动原先运行的服务后，才能结束维护。

只有 PostgreSQL 自定义格式导出、角色定义（不含角色密码）、各表行数、本地对象
归档都成功，且已停止的服务恢复启动后，`manifest.json` 才记录 `complete`。
对象卷缺失、导出或归档错误、服务重启失败均以非零状态退出；残留产物标为
`partial`，不能恢复。Manifest 包含版本、源配置标识、逐文件大小与 SHA-256，以及
每个归档对象的内容摘要。`verify-backup.sh` 可离线检查这些信息，并拒绝链接、
路径越界和不完整备份。摘要用于发现损坏，不能认证不可信备份。请将整个目录
（包括 Manifest）保存到有访问控制的加密备份存储；数据库导出包含用户密码散列
和业务数据。原有加密密钥、签名密钥及运行时凭据必须另存到密钥管理系统，恢复
业务功能时仍需使用。

当前流程只支持端点为 `opencitadel-minio:9000` 的本地命名卷 MinIO。外部 MinIO、
COS/S3、目录绑定挂载及自定义存储必须使用对应供应商的一致性对象快照方案；脚本
会直接失败，不会把仅数据库副本标为完整备份。Redis 属于可重建的协调与缓存状态，
不在备份范围；沙箱临时文件和日志也不包含。Helm 的 PostgreSQL CronJob 仍只是
数据库备份，不能代替完整应用恢复。

恢复命令先校验所有产物，之后才访问 Docker。目标必须是全新的 `restore_<名称>`，
如果存在同名目标容器或卷则拒绝执行。脚本创建本次调用专用的新卷，以及无网络、
不发布端口的 PostgreSQL 容器。它先恢复角色，再以遇错即停方式恢复数据库，比较
所有表的行数，并只向全新的空对象卷解压归档。之后重新读取对象卷，逐文件比较
大小与摘要。检查结束后停止恢复容器，保留卷用于人工验收。失败恢复保留
`partial` 报告；下一次应换新目标名，不覆盖残留数据。

`restore-<目标>.json` 记录准确的容器名、卷名、恢复超级用户、源 Manifest 摘要；
数据验证成功时标为 `payload_verified`，但明确保留
`application_smoke_verified: false`。这是恢复演练，不会自动切换生产环境。按报告
中的资源标识，将恢复卷挂到另外隔离的 Compose 项目进行应用验收。使用备份时的
PostgreSQL/MinIO 镜像版本与应用代码版本，从密钥管理系统重新设置运行时角色密码
（角色备份不含密码），保留原加密/签名密钥，并配置恢复后的 MinIO 地址与凭据。
演练时关闭调度、集成与对外通知，验收界面只绑定本机。不要把恢复卷挂到在线项目，
也不要用新建空应用覆盖恢复数据。

批准切换前，应在隔离应用中记录以下验收：

1. 已知本地用户能登录和修改密码，旧会话失效。
2. 历史会话与代表性附件可打开。
3. 已知知识库文档能够检索，存储内容能够读取。
4. 已导出的证据包使用原签名密钥通过签名与内容校验
   （`scripts/verify_evidence_package.py`）。
5. 在不派发生产动作的前提下检查待执行任务与审批状态，验收后才开启外部集成。

无需 Docker 守护进程即可运行编排回归测试：

```bash
python3 -m unittest discover -s scripts/tests -p test_backup_tooling.py -v
```

这些测试使用记录命令的 Docker 替身和合成数据库/对象数据，验证失败处理、参数、
执行顺序及 Manifest/内容校验，不能替代真实 Docker/PostgreSQL/MinIO 恢复和应用演练。
真实恢复需要运行中的 Docker，以及源 PostgreSQL 镜像和 `alpine:3.20`（缺少镜像时
Docker 可能拉取）。

## 执行异常与校验恢复

管理台展示空间投影滞后、隔离 Run、planner 自动重试时间及持久化恢复结果。
planner 异常分别在 5 秒、10 秒后重试，第三次失败隔离该 Run，并向所属用户或团队
保存站内提示；其他 Run 继续执行。管理员在“执行恢复”填写 `user:<id>` 或
`team:<id>` 及恢复原因，提交后刷新查看 pending、completed、partial 或 failed。

只有执行内核能重建投影。恢复期间该空间暂停新的决策及 Activity 派发；内核回放
原始事件并核验状态和必要决策输入后，只释放校验通过的 Run。原始输入缺失或损坏
的 Run 保持隔离，partial 结果列出其 ID；修复数据或代码后再试。恢复过程留有审计，
不会重放结果未知的外部写操作。使用内核数据库凭据运行
`python -m app.rebuild_execution_projection --scope user:<id>` 也会执行上述校验，
并报告实际解除隔离的 Run 数量。

关闭业务调度只停止新定时任务，在途对账、保留清理、连接回收、通知投递、复检和
恢复继续运行。投递、复检和恢复各有独立受监管循环。准入硬上限在同一数据库事务
内统计待处理及活跃工作流；父子 Run 共享容量，全部终态才释放。超限的新执行链
返回 HTTP 429，已有执行链重试不重复占用。

本次模型变更按新项目初始化数据库。已经应用初始迁移的数据库不会因再次运行
同一迁移自动新增表或字段。`app.migrate` 会将当前迁移链的未应用 Revision 升级到
Head；禁止将新建库验收指向生产数据。
