[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel API 服务

基于 FastAPI 构建的后端 API 服务，提供会话管理、AI Agent 调度、模型管理、Skill 模板、长期记忆、文件处理、沙箱管理等核心功能。

Agent 任务由**独立 Worker 进程**执行，API 层无状态，支持多副本水平扩展。

## 技术栈

- Python 3.12+
- FastAPI + Uvicorn
- SQLAlchemy (asyncpg) + Alembic + **pgvector**
- Redis 7（任务 dispatch 消费组 + Streams 事件管道）
- Docker SDK + SandboxProvider（沙箱池化抽象）
- Playwright（浏览器控制在 Worker；Chromium 在沙箱内通过 CDP 连接）
- OpenTelemetry + Prometheus（可选可观测性）
- MCP SDK / httpx（MCP、A2A、Anthropic、Gemini）

## 架构概览

API 与 Worker 为独立进程，共享 `BaseContainer` 中的基础设施与服务提供者，各自使用角色容器：

| 角色 | 入口 | 容器 | Docker target |
|------|------|------|---------------|
| API | `app.main` | `ApiContainer` | `api` → `opencitadel-api` |
| Worker | `app.worker.main` | `WorkerContainer` | `worker` → `opencitadel-worker` |
| Migrate | `app.migrate` | 无 | `api`（一次性 job） |

- **API**：HTTP/SSE、任务 dispatch、事件流读取、MCP/A2A 连接池回收
- **Worker**：消费 `task:dispatch`、运行 Agent、Codebase/KB 摄取、沙箱预热门户与孤儿清理
- **Migrate**：独立 job（`python -m app.migrate`），API 启动时仅校验 schema 版本

完整架构说明见 [`../docs/architecture/overview.zh-CN.md`](../docs/architecture/overview.zh-CN.md)。

## Worker 与 Migrate 角色

### Worker（`app.worker.main`）

Worker 是 Redis `task:dispatch` 的**长驻消费者**。任何 Agent、代码库摄取、知识库摄取任务都必须有 Worker 才能执行。

| `task_type` | 合成 session id | Runner |
|-------------|-----------------|--------|
| `agent`（默认） | 用户 session id | `TaskRunnerFactory` → `AgentTaskRunner` |
| `codebase_ingest` | `codebase-ingest:{id}` | `CodebaseIngestionTaskRunner` |
| `kb_ingest` | `kb-ingest:{id}` | `KBIngestionTaskRunner` |

除任务执行外，Worker 还负责：

- 任务租约获取/续期/释放（`task_lease.py`），崩溃后幂等
- 沙箱准入、预热池与孤儿回收（`sandbox_maintenance.py`）
- 启动与周期 reconcile 卡住的 KB 摄取
- 可选 DLQ 回放（`model_resilience.dlq_replay_enabled=true`）
- 关闭时释放 MCP/A2A 出站连接池

Worker 可水平扩展；各副本加入同一 Redis 消费组。见 [任务恢复](../docs/architecture/task-recovery.zh-CN.md)。

### Migrate（`app.migrate`）

Migrate 是**每次部署的一次性 job**，非长驻服务：

1. **Alembic** schema 升级到 `head`
2. **数据迁移**：LLM API Key 加密、AppConfig YAML 种子、MCP/A2A blob 迁移、MCP URL/secret 迁移

```bash
./migrate.sh
# 或: python -m app.migrate
```

API 启动时校验 schema 是否为 Alembic head，**未迁移则拒绝启动**（test 环境跳过）。Docker Compose 在 API/Worker 前运行 `opencitadel-migrate`；Helm 使用 migrate initContainer。

勿在 migrate 进程中运行 Agent 任务 — 其未装配 `WorkerContainer`。

## 项目结构

```
api/
├── app/
│   ├── interfaces/            # FastAPI 路由、schemas、中间件、认证 DI
│   ├── application/
│   │   └── services/          # AgentService, TaskRunnerFactory, LLM*Service...
│   ├── domain/
│   │   ├── models/            # 领域实体（session、llm_endpoint、event...）
│   │   ├── repositories/      # UoW 与仓储端口
│   │   ├── external/          # 外部服务端口
│   │   ├── services/agents/   # Planner、Clarify、ReAct、SubAgent
│   │   ├── services/flows/    # PlannerReActFlow、CodeAskFlow、DocQAFlow、HybridAskFlow
│   │   └── schemas/           # 结构化 LLM 输出
│   ├── infrastructure/
│   │   ├── repositories/      # DB 仓储实现
│   │   ├── adapters/          # Redis、存储、事件投影适配器
│   │   ├── external/task/     # RedisStreamTask、TaskStateService
│   │   ├── external/sandbox/  # DockerSandbox、SandboxProvider
│   │   ├── external/llm/      # OpenAI、ResilientLLM、熔断器
│   │   ├── observability/     # OTel、AgentTracer、logging_context
│   │   └── security/          # ApiKeyCipher、SecretManager
│   ├── runtime_role.py        # ProcessRole (api/worker/migrate)
│   ├── container.py           # BaseContainer / ApiContainer / WorkerContainer
│   ├── worker/main.py         # Agent Worker 入口
│   ├── migrate.py             # 独立迁移入口
│   └── main.py                # FastAPI 入口
├── alembic/
├── core/config.py
├── migrate.sh / worker.sh / run.sh
└── Dockerfile
```

## API 路由

以下路径默认前缀为 `/api`。除公开路由外，鉴权路由需有效会话 JWT（部分集成接口支持 `X-Api-Key`）。

> **维护说明**：本路由表为手工维护。新增路由时请同步更新本文件与 `README.md`，并对照 `app/interfaces/endpoints/routes.py` 与在线 OpenAPI `/openapi.json`。提交文档 PR 前运行 `./scripts/check-docs.sh`。

### 公开 / 无需登录

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register`、`/auth/login`、`/auth/refresh`、`/auth/logout` | Cookie 会话认证 |
| GET | `/auth/oauth/{provider}/login`、`/auth/oauth/{provider}/callback` | OAuth（Google/GitHub） |
| GET | `/status` | 健康检查 |
| GET | `/llm/status` | LLM 可用性摘要 |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/marketplace/apps` | 应用市场目录 |
| POST | `/marketplace/*` | 应用市场 mini-app 接口 |
| POST | `/webhooks/{job_token}` | 自动化 Webhook 入口（路径含 token） |
| GET | `/share/artifact/{token}` | 公开交付物分享（路径含 token） |
| GET | `/invitations/{token}` | 团队邀请预览 |
| POST | `/invitations/{token}/register` | 通过团队邀请注册并入队 |
| GET | `/.well-known/agent-card.json` | A2A agent card（功能开关启用时） |

### 需登录（会话 JWT）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/auth/me` | 当前用户资料 |
| POST | `/invitations/{token}/accept` | 接受团队邀请（已登录用户） |
| GET/POST/DELETE | `/service-keys` | 服务 API Key CRUD |
| GET/POST/PATCH/DELETE | `/teams`、`/teams/{id}/*` | 团队工作区 API |
| GET/POST/PATCH | `/sessions`、`/sessions/{id}/*` | 会话 CRUD、chat SSE、检查点、VNC |
| GET/POST/PUT/DELETE | `/skills`、`/memories`、`/files` | Skill、长期记忆、文件 |
| GET/POST | `/codebases`、`/knowledge-bases`、`/scheduled-jobs`、`/notifications` | 代码库、知识库、自动化、通知 |
| GET/PUT/DELETE | `/app-config/*`、`/llm-endpoints`、`/llm-models` | AppConfig、LLM 端点与模型 |

### 服务 API Key（`X-Api-Key`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/a2a` | 入站 A2A（功能开关控制；需有效服务 API Key） |

> 服务 API Key 仅认证调用方，**不携带**团队工作区 scope。团队作用域操作请使用会话 JWT + `X-Workspace-Id`。

### 管理与合规（审计员或管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/PATCH/DELETE | `/admin/users`、`/admin/users/{id}` | 用户管理 |
| POST | `/admin/invitations` | 平台邀请 |
| GET/PUT | `/admin/users/{id}/quota` | 用户配额 |
| GET | `/admin/audit`、`/admin/audit/{id}`、`/admin/audit/summary`、`/admin/audit/export` | 审计日志 |
| GET | `/admin/usage`、`/admin/usage/summary`、`/admin/usage/timeseries`、`/admin/usage/breakdown` | Token 用量 API |
| GET | `/admin/overview` | 管理概览指标 |
| GET/DELETE/PATCH | `/admin/teams`、`/admin/teams/{id}` | 团队管理 |
| GET | `/admin/audit/verify-chain`、`/admin/audit/verify-chain/sessions/{id}` | 审计链校验 |
| GET | `/admin/evidence/sessions`、`/admin/evidence/sessions/{id}/package` | 合规证据包 |
| GET | `/admin/compliance/report` | 合规报告导出 |

### 团队与邀请

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/teams` | 列表/创建团队 |
| GET/DELETE | `/teams/{id}` | 团队详情/删除 |
| GET/POST/DELETE/PATCH | `/teams/{id}/members`、`/teams/{id}/invitations` | 成员与邀请（邀请可选 `email`） |
| POST | `/teams/{id}/leave` | 退出团队 |
| GET | `/invitations/{token}` | 预览邀请（公开） |
| POST | `/invitations/{token}/register` | 注册并入队（公开） |
| POST | `/invitations/{token}/accept` | 接受邀请 |

### 应用配置与集成

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/app-config/sections` | AppConfig 分段列表 |
| GET/PUT/DELETE | `/app-config/sections/{section}` | 读取/更新/删除分段 |
| GET/POST | `/app-config/revisions`、`/app-config/revisions/{id}/rollback` | 配置修订历史 |
| GET/PUT | `/app-config/agent` | Agent 配置快捷入口 |
| GET/POST/PUT/DELETE | `/app-config/mcp-servers`、`/app-config/mcp-servers/{name}/*` | MCP 服务 CRUD |
| GET/POST/DELETE | `/app-config/a2a-servers`、`/app-config/a2a-servers/{id}/*` | A2A 服务 CRUD |

### LLM 端点与模型

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/llm-endpoints` | 列表/创建 Provider 端点（存储加密 API Key） |
| GET/PUT/DELETE | `/llm-endpoints/{id}` | 端点 CRUD |
| GET/POST | `/llm-models` | 列表/创建模型（引用 `endpoint_id`） |
| GET/PUT/DELETE | `/llm-models/{id}` | 模型 CRUD |
| POST | `/llm-models/{id}/set-default` | 设置默认模型 |
| POST | `/llm-models/{id}/probe-multimodal` | 探测多模态能力 |

详见 [`../docs/architecture/llm-endpoints-and-models.zh-CN.md`](../docs/architecture/llm-endpoints-and-models.zh-CN.md)。

### 会话、交付物、Skill、记忆、文件

| 方法 | 路径 | 说明 |
|------|------|------|
| POST/GET/PATCH | `/sessions`、`/sessions/stream`、`/sessions/{id}` | 会话 CRUD 与列表 |
| POST | `/sessions/{id}/chat` | SSE 流式对话 |
| GET | `/sessions/{id}/events` | 分页事件回放 |
| GET/POST | `/sessions/{id}/memory`、`/memory/compact`、`/memory/clear` | 会话 Agent 记忆 |
| GET/POST | `/sessions/{id}/checkpoints`、`/checkpoints/{id}/restore` | 检查点与回滚 |
| WS | `/sessions/{id}/vnc` | VNC WebSocket 代理 |
| GET/POST | `/sessions/{session_id}/artifacts`、`/artifacts/{id}`、`/artifacts/{id}/share` | 交付物 |
| GET/POST/PUT/DELETE | `/skills`、`/skills/recommend`、`/skills/import` | Skill 模板 |
| GET/POST/PUT/DELETE | `/memories` | 长期记忆 |
| POST/GET | `/files`、`/files/{id}/download` | 文件上传/下载 |

### 代码库、知识库、自动化

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/codebases`、`/codebases/{id}/*` | 代码导入、摄取 SSE、符号、会话 |
| GET/POST | `/knowledge-bases`、`/knowledge-bases/{id}/*` | 知识库 CRUD、文档、摄取、重建索引 |
| GET/POST/PATCH/DELETE | `/scheduled-jobs`、`/scheduled-jobs/{id}/*` | Cron/Webhook 任务 |
| GET/POST | `/notifications`、`/notifications/stream` | 用户通知 |

### SSE 事件类型

所有 SSE `data` 都带有 `event_id`、`created_at`、`schema_version`、`visibility`、`channel`、`persist` 元信息。历史事件通过 `GET /api/sessions/{id}/events?after=<seq>&limit=100` 分页重放，实时事件通过 `/chat` SSE 推送。

| 事件 | 说明 |
|------|------|
| `clarify` | ClarifyAgent 澄清问题 |
| `message` | 用户或助手完整消息 |
| `message_delta` | 助手文本增量（按 `stream_id` 合并） |
| `reasoning_delta` | 思考内容增量（默认仅 `include_debug=true`） |
| `tool_args_delta` | 工具参数 JSON 增量（默认仅 `include_debug=true`） |
| `assistant_notice` | 面向用户的助手提示 |
| `session_status` | 服务端权威会话状态 |
| `debug_item` | 内部调试项（`include_debug=true`） |
| `title` | 会话标题更新 |
| `plan` | 计划事件（含 steps 列表） |
| `step` | 步骤事件（含 id/status/description） |
| `subagent` | 子 Agent 委派状态 |
| `tool` | 工具调用事件（含 name/function/args/content） |
| `artifact` | 交付物工作台更新 |
| `approval` | 计划/工具审批门控状态 |
| `wait` | 等待用户输入 |
| `usage` | Token 用量事件 |
| `done` | 流结束 |
| `error` | 错误事件 |

更多设计细节见 [`../docs/architecture/events.zh-CN.md`](../docs/architecture/events.zh-CN.md)。

## Agent 能力

- **Token 流式**：`LLM.stream_invoke()` 逐 delta 推送至 SSE
- **并行工具**：单轮多 `tool_calls` 并发；browser/shell 自动加锁
- **结构化 Planner 输出**：`PlannerPlanSchema` Pydantic 严格校验
- **向量记忆**：`api/config.yaml` 中 `memory.vector_enabled: true` 时启用 pgvector 混合召回（默认 **false**）。知识库索引使用独立的 `knowledge_base.vector_enabled` 开关（默认 **true**）。
- **多 Provider**：OpenAI 兼容 / Anthropic / Gemini 原生适配

## 本地开发

### 环境准备

```bash
pip install uv
uv sync --frozen
playwright install
```

### 配置环境变量

参考 `.env.example`（启动引导与密钥）和 `api/config.yaml`（运行时行为）。关键 `.env` 配置：

```bash
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=opencitadel
POSTGRES_HOST=localhost
REDIS_HOST=localhost
REDIS_PORT=6379
API_KEY_SECRET=            # 生产环境必须设置强随机值
EMBEDDING_API_KEY=         # 向量记忆启用时填写
```

沙箱地址、向量记忆、OTEL 等开关在 `api/config.yaml`：

```yaml
sandbox:
  address: null             # 留空则动态创建沙箱容器
memory:
  vector_enabled: false
observability:
  otel_enabled: false
```

### 启动服务

```bash
# 1. 数据库迁移（必须先执行）
./migrate.sh

# 2. 启动 API
./run.sh
# 或: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3. 另开终端启动 Worker（必须，否则 Agent 任务不会执行）
./worker.sh
```

访问 `http://localhost:8000/docs` 查看 API 文档。

### 数据库迁移

```bash
# 推荐：独立迁移脚本（Alembic + 数据迁移/配置种子）
./migrate.sh
# 或
python -m app.migrate

# 开发：生成新迁移
alembic revision --autogenerate -m "描述"
# 应用迁移仍使用完整入口
./migrate.sh
```

> **注意**：API 启动时会校验 DB schema 是否为 Alembic head，未迁移将拒绝启动（test 环境跳过）。

### LLM API Key 加密迁移

生产环境需在 `.env` 设置强随机 `API_KEY_SECRET`（`openssl rand -hex 32`）。`llm_endpoints.api_key_encryption` 标识存储格式：

| 值 | 含义 |
|----|------|
| `legacy_plaintext` | 历史明文（兼容读取） |
| `fernet_v1` | 使用 `API_KEY_SECRET` 加密 |

`python -m app.migrate`（或 Docker Compose 的 `opencitadel-migrate`）会在 Alembic 后自动加密历史明文。单独修复时可运行 `python -m app.migrate_llm_api_keys`。

API 与 Worker 在 `ENV=production` 时都会校验弱密钥并拒绝启动。

## Docker 部署

`Dockerfile` 为多阶段构建，产出独立镜像：

| target | 镜像 | CMD |
|--------|------|-----|
| `api` | `opencitadel-api` | `./run.sh` |
| `worker` | `opencitadel-worker` | `./worker.sh` |

通过根目录 `docker-compose.yml` 统一部署：

| 服务 | 说明 |
|------|------|
| `opencitadel-migrate` | 一次性 init job（api target），执行 Alembic + LLM Key 迁移 |
| `opencitadel-api` | FastAPI 无状态 API（`target: api`） |
| `opencitadel-worker` | Agent Worker 池（`target: worker`，可 scale） |
| `opencitadel-postgres` | `pgvector/pgvector:pg16` |
| `opencitadel-redis` | 任务队列与事件流 |

```bash
docker compose up -d --build
docker compose logs -f opencitadel-worker
```

构建期 `pip install uv` 与 `uv sync` 默认走阿里云 PyPI（见根目录 `docker-compose.yml` 的 build args）。可通过环境变量 `PIP_INDEX_URL`、`UV_INDEX_URL`、`UV_VERSION`、`UV_HTTP_TIMEOUT` 覆盖。

## Kubernetes

Helm Chart 位于 [`../deploy/helm/opencitadel/`](../deploy/helm/opencitadel/)，包含 API/Worker Deployment、HPA 与 migrate initContainer。
