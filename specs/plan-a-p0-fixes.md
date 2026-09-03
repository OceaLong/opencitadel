# Plan A · P0 修复包（Phase 0，预估 1–2 天）

原则：只修断链和陷阱，不做功能扩展；每项独立可验收、独立成 patch 备份。

## A-1 沙箱执行链路交付面修复

**现状**：2026-09-03 已在本机手工修复（镜像已构建、`.env` 的 `SANDBOX_NETWORK` 已注释），但交付面仍有两个坑会让下一个全新环境重现故障。

1. `scripts/quickstart.sh` 增加 `docker compose build opencitadel-sandbox`（或把该服务移出 `fixed-sandbox` profile、并入默认构建集），保证裸 quickstart 后沙箱可用。参考 `Makefile:12` 已包含该目标。
2. `.env.example` 中 `SANDBOX_NETWORK` 行改为注释+说明：留空使用 compose 默认（带项目前缀 `${COMPOSE_PROJECT_NAME}_opencitadel-sandbox-network`），显式设无前缀名会导致 broker 报 `network not found`。
3. （可选加固）broker 在 `create_sandbox` 捕获 `docker.errors.NotFound` 时，把"镜像不存在/网络不存在"翻译成明确的 4xx 错误信息，而不是笼统的 `broker rejected request`（`api/app/infrastructure/external/sandbox/broker.py:121`）。

**验收**：从零卷 + 干净 `.env`（由 example 生成）跑 quickstart，60 秒内 `docker ps --filter ancestor=opencitadel-sandbox` 出现容器，kernel 日志无 reconciliation failed。

## A-2 CI api-test 必挂：pg_trgm

- `api/scripts/prepare_ci_database.py:81-83` 的 superuser 段补 `CREATE EXTENSION IF NOT EXISTS pg_trgm;`（与 `deploy/helm/opencitadel/files/postgres/init-app-role.sh` 的本地修复对齐，后者已暂存）。
- 顺手核对迁移 `api/alembic/versions/0001greenfield_initial.py:94` 需要的全部扩展在 CI 预置脚本中齐全。

**验收**：CI `api-test` job 的迁移步骤通过（或本地模拟：非超级用户角色跑 `alembic upgrade head` 于预置库）。

## A-3 RLS 签名密钥 split-brain 根修

**问题链**：DB 侧 HMAC 密钥在首次迁移时一次性写死为 `settings.session_secret`（`api/alembic/env.py:93-99`、`api/app/migrate_runtime_policy_seed.py:22`，单行表 `execution_authorization_secrets` 无更新路径）；运行时 composition 路径读 `settings.database_authorization_signing_secret`（`api/app/composition/shared.py:347`），而 `postgres.py:67` 的 session_factory 又直接用 `session_secret`——三处来源，两两可能不一致。

**修法（三步）**：
1. 统一取值：`env.py`、`migrate_runtime_policy_seed.py`、`postgres.py:67` 全部改用 `settings.database_authorization_signing_secret`（其默认值已回退到 session_secret，全新库行为不变）。
2. 轮换工具：新增可重入 CLI（如 `python -m app.rotate_db_signing_secret`），在事务里 `UPDATE execution_authorization_secrets` 并验证一条 RLS 查询能通过；文档 `docs/operations/deployment.md:143-147` 同步补操作步骤。
3. 护栏：contract 测试禁止 `session_secret` 出现在 db_authorization 相关调用点；`.env.example:51` 加显式警告"仅可在首次迁移前设置，之后改动需跑轮换工具"。

**验收**：a) 全新库 + 显式设置该变量 → 启动播种成功（此前必挂）；b) 存量库跑轮换工具后 API 正常读写；c) contract 测试落地。

## A-4 配额下沉到 SessionService

- 把 `check_session_quota`（现仅 `session_routes.py:78` 调用）下沉到 `SessionService.create_session` 内部，覆盖全部 5 条绕过路径：A2A（`a2a_server_service.py:181/226`）、定时任务（`scheduled_job_service.py:433`）、KB 会话（`knowledge_base_service.py:944`）、Patrol（`patrol_run_service.py:178`）。
- 增加 per-source 豁免参数（如 `quota_exempt: bool`，默认 False），系统内部维护类会话（Patrol 系统巡检）可显式豁免并留审计记录，避免一刀切改变既有行为。

**验收**：单测覆盖 5 条路径均受 `max_concurrent_tasks`/`daily_session_limit` 约束；豁免路径有审计日志。

## A-5 前端"错误伪装成空态"批量修复

一次性给以下位置补 `catch` + i18n 错误提示（新增可复用的 `useLoadError` 或直接 toast+页面 error 态）：

| 位置 | 现症状 |
|---|---|
| `ui/src/hooks/use-paginated-list.ts:60-73` | 所有分页列表失败=空列表（影响审计/任务运行等） |
| `ui/src/app/admin/page.tsx:65-99` | 9 个并发请求任一失败 → 全 0 指标无提示 |
| `ui/src/app/admin/compliance/page.tsx:27-38` | 失败显示"暂无证据会话"（安全语义风险） |
| `ui/src/app/admin/governance/page.tsx:33-37` | 同上，全 0 指标 |
| `ui/src/app/admin/invitations/page.tsx:41` | 列表失败静默 |
| `ui/src/app/admin/audit/page.tsx:96-103,131-138` | 链验证/详情失败静默 |
| `ui/src/app/admin/compliance/report/page.tsx:43-56` | 生成失败 spinner 停止无反馈 |

**验收**：mock 接口 500，以上每页均出现明确错误提示而非空态/全零；`npm run test`、`npm run i18n:check` 通过。

## A-6 `.env.example` 开箱即用 + 仓库卫生

1. `.env.example` 默认组合改为可直接启动：`ENV=development`、`COMPOSE_PROFILES=local`、`STORAGE_PROVIDER=minio`（cos 段保留为注释），与 `scripts/quickstart.sh:82-96` 的改写逻辑对齐。
2. 补齐缺失的可选变量段并注明"留空=功能关闭"：`SMTP_*`（6 项）、`CORS_ORIGINS`、`OTEL_ENABLED/SERVICE_NAME/EXPORTER_ENDPOINT`、`POSTGRES_POOL_*` 等（对照 `api/core/config.py` 差集，共约 25 项）。
3. `scratch_config.yml`：`git rm --cached scratch_config.yml`（保留本地文件）+ `.gitignore` 加规则。含本机绝对路径与全部 env 键值，若哪天用真实 `.env` 重生成会直接泄密。改动只暂存不 commit。
4. 死配置清理：删除 `EXECUTION_IDLE_POLL_SECONDS`（`core/config.py:170`、`.env.example:191`，全仓零读取）或将其接进 `ExecutionKernelProcess`（`execution_kernel_main.py:38` 硬编码 1.0）——二选一，倾向接通。
5. 过期 TODO 清理（会误导维护者）：`activities/model_call.py:62-64`、`infrastructure/models/user_quota.py:63`、`core/config.py:163`。

**验收**：`cp .env.example .env && make quickstart` 一次起成（含沙箱）；`git status` 无 scratch_config.yml 跟踪。

## A-7 acceptance 栈解阻

- `.env.e2e:24,71` 的 `TRUSTED_PROXY_CIDRS=10.0.0.0/8` 改为 `127.0.0.1/32,::1/128`（compose 栈 nginx 同宿主），使其通过 HEAD 新增的 `_is_overbroad_private_proxy_cidr` 校验（`api/core/config.py:344-362`）。
- helm 默认值同题（`values.yaml:290`）归入 Plan D。

**验收**：`scripts/acceptance/runner.py` 能起栈跑通至少 identity 套件。

## 执行顺序建议

A-1 → A-2 → A-7（解阻类，半天）→ A-3（最重，需全新库+存量库双验证）→ A-4 → A-5 → A-6。每项完成后：改动 `git add` 暂存、`git diff --cached > scratchpad patch` 备份。
