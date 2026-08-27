# OpenCitadel API 与执行内核

[English](README.md)

Python 后端包含三个明确进程角色。PostgreSQL 执行事件是唯一工作流事实，
Redis 仅是可丢失的唤醒通道。

| 角色 | 入口 | 职责 |
| --- | --- | --- |
| API | `app.main` / `run.sh` | 认证、授权、Command 准入、投影查询、SSE |
| 执行内核 | `app.execution_kernel_main` / `execution-kernel.sh` | Inbox、决策、Activity、Timer、Outbox、投影、Scheduler |
| Migrate | `app.migrate` / `migrate.sh` | 全新 Alembic Schema 与类型化 Runtime Policy Seed |

API 不执行 Agent 或摄取步骤。执行内核轮询 PostgreSQL 中的持久工作，也可等待
Redis 提示。删除 Redis 不会删除已接受的 Command、Activity、Timer、Event 或结果。

## 技术栈

- Python 3.12、FastAPI、Pydantic 2
- SQLAlchemy 2 async、Alembic、PostgreSQL 16、pgvector
- Redis 7（只用于唤醒提示和缓存）
- OpenAI、Anthropic、Gemini 模型适配器
- MCP、A2A、Playwright、Docker/Kubernetes 沙箱
- OpenTelemetry 与 Prometheus

## 源码地图

```text
app/
├── domain/execution/           强类型 Command、Event、Aggregate、Policy
├── application/execution/      编排、决策、Activity、Projector
├── infrastructure/execution/   PostgreSQL Store 与 Redis 唤醒适配器
├── composition/                手工强类型 API/Kernel 对象图与任务所有权
├── interfaces/                 FastAPI 路由、Schema、认证依赖
├── application/services/       产品应用服务
├── domain/                     产品实体与端口
├── infrastructure/             仓储、Provider、安全、可观测性
├── execution_kernel.py         仅应用层的内核编排
├── execution_kernel_main.py
├── migrate.py
└── main.py
alembic/versions/0001greenfield_initial.py
```

所有非确定 Provider 工作都建模为 Activity。外部调用前必须提交 Invocation 身份、
输入摘要、超时、策略快照和 call-start 状态；完成结果通过强类型 Command 回写。
Run、Activity、审批、资源构建和公开事件表都是可重建投影，不是第二状态机。

## 装配与事务

`app.main:create_app --factory` 只加载一次部署配置，并把 Lifespan 所有的 `ApiRuntime`
安装到 `app.state`。`app.execution_kernel_main` 构建独立 `KernelRuntime`。
`TaskSupervisor` 持有全部后台协程并执行有界排空；两个角色不共享资源实例。

Application Mutation 显式调用 `uow.commit()`。Context 未提交即退出时一律 rollback，
包括正常 return。Repository 永不 commit；Redis 发布只能在 PostgreSQL 成功后的
post-commit 阶段作为提示发生。

`/api/health/live` 用于进程 Liveness，`/api/health/ready` 用于完整 Runtime Readiness。
`/api/status` 是依赖诊断，不是生命周期探针。

## 安全边界

认证请求解析为不可变 `AuthorizationContext` 与 `OwnerScope`。事务级 PostgreSQL
设置驱动强制 RLS。全新部署分别创建应用、执行内核和迁移角色；运行时角色不拥有 schema。

- 用户资源属于个人或单一团队工作区。
- Auditor 只读。
- Admin 管理全局资源与平台配置。
- 跨 scope 查询关闭失败，通常返回未找到。
- LLM 与集成 Secret 只使用版本化 `fernet_v2` 信封。

## 核心 HTTP 契约

应用路由统一位于 `/api`：

- `/auth/*`、`/teams/*`、`/service-keys/*`：身份与工作区
- `/sessions/*`：会话 CRUD、消息 Command 准入、公开事件回放、VNC 与文件
- `/runs/*`、`/approval-batches/*`：正式执行与审批 Command
- `/knowledge-bases/*`、`/codebases/*`：不可变候选构建与已发布版本绑定
- `/scheduled-jobs/*`、`/patrol-*`：自动化、巡检、证据、修复
- `/inference/endpoints/*`、`/inference/models/*`、`/inference/bindings/*`、
  `/skills/*`、`/runtime-policies/*`：运行资源、策略版本与推理绑定
- `/admin/*`：用户、用量、审计、治理、合规

路由级事实以 `/openapi.json` 为准。

## 本地开发

```bash
uv sync
uv run pytest -q
uv run lint-imports
uv run ruff check --select F821 app tests
```

配置 `.env` 与 PostgreSQL 后，在不同终端运行：

```bash
./migrate.sh
./run.sh
./execution-kernel.sh
```

迁移只有一个全新 schema revision；不存在历史数据转换命令或备用执行 schema。

## 容器

Dockerfile 提供 `api` 与 `execution-kernel` target。Compose 服务名是
`opencitadel-api`、`opencitadel-execution-kernel` 和 `opencitadel-migrate`。
Helm 使用相同的 API/Kernel 分离与独立凭据。

参见[架构概览](../docs/architecture/overview.zh-CN.md)、
[执行内核](../docs/architecture/execution-kernel.zh-CN.md)与
[部署指南](../docs/operations/deployment.zh-CN.md)。
