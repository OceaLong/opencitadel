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
- **Worker**：消费 `task:dispatch`、运行 Agent、沙箱预热门户与孤儿清理
- **Migrate**：独立 job（`python -m app.migrate`），API 启动时仅校验 schema 版本

完整架构说明见 [`../docs/architecture/overview.zh-CN.md`](../docs/architecture/overview.zh-CN.md)。

## 项目结构

```
api/
├── app/
│   ├── application/
│   │   ├── services/          # AgentService, TaskRunnerFactory, MemoryService...
│   │   └── ...
│   ├── domain/
│   │   ├── services/agents/   # BaseAgent, Planner, ReAct
│   │   ├── services/flows/    # PlannerReActFlow
│   │   └── schemas/           # PlannerPlanSchema 等结构化输出
│   ├── infrastructure/
│   │   ├── external/task/     # RedisStreamTask, TaskStateService
│   │   ├── external/sandbox/  # DockerSandbox, SandboxProvider
│   │   ├── external/llm/      # OpenAI, Anthropic, Gemini
│   │   ├── observability/     # OTel, AgentTracer, logging_context
│   │   └── security/          # ApiKeyCipher, SecretManager
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

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 健康检查 |
| GET | `/api/metrics` | Prometheus 指标 |
| GET/POST | `/api/app-config` | 应用配置管理 |
| GET/POST | `/api/llm-models` | 模型列表与创建 |
| GET/PUT/DELETE | `/api/llm-models/{id}` | 模型详情、更新与删除 |
| POST | `/api/llm-models/{id}/set-default` | 设置默认模型 |
| GET/POST | `/api/skills` | Skill 模板列表与创建 |
| GET/PUT/DELETE | `/api/skills/{id}` | Skill 模板详情、更新与删除 |
| GET/POST | `/api/memories` | 长期记忆列表与创建 |
| GET/PUT/DELETE | `/api/memories/{id}` | 长期记忆详情、更新与删除 |
| POST | `/api/files` | 文件上传 |
| GET | `/api/files/{id}/download` | 文件下载 |
| POST | `/api/sessions` | 创建会话 |
| POST | `/api/sessions/stream` | SSE 流式获取会话列表 |
| GET | `/api/sessions/{id}` | 获取会话详情 |
| GET | `/api/sessions/{id}/events` | 按游标分页获取会话事件 |
| PATCH | `/api/sessions/{id}` | 更新会话模型或 Skill 配置 |
| POST | `/api/sessions/{id}/chat` | SSE 流式对话（含 Token delta 事件） |
| GET | `/api/sessions/{id}/memory` | 获取会话 Agent 内存 |
| POST | `/api/sessions/{id}/memory/compact` | 压缩会话 Agent 内存 |
| POST | `/api/sessions/{id}/memory/clear` | 清空会话 Agent 内存 |
| DELETE | `/api/sessions/{id}/memory/{agent_name}/messages/{index}` | 删除指定会话内存消息 |
| WS | `/api/sessions/{id}/vnc` | VNC WebSocket 代理 |
| GET/POST | `/api/codebases` | 代码知识库导入与管理 |
| GET | `/api/codebases/{id}/ingest` | 代码库摄取进度 SSE |
| GET/POST | `/api/knowledge-bases` | 文档知识库管理 |
| GET/POST | `/api/knowledge-bases/{id}/documents` | 文档上传与摄取 |

### SSE 事件类型

所有 SSE `data` 都带有 `event_id`、`created_at`、`schema_version`、`visibility`、`channel`、`persist` 元信息。历史事件通过 `GET /api/sessions/{id}/events?after=<seq>&limit=100` 分页重放，实时事件通过 `/chat` SSE 推送。

| 事件 | 说明 |
|------|------|
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
| `tool` | 工具调用事件（含 name/function/args/content） |
| `wait` | 等待用户输入 |
| `usage` | Token 用量事件 |
| `done` | 流结束 |
| `error` | 错误事件 |

更多设计细节见 [`../docs/architecture/events.zh-CN.md`](../docs/architecture/events.zh-CN.md)。

## Agent 能力

- **Token 流式**：`LLM.stream_invoke()` 逐 delta 推送至 SSE
- **并行工具**：单轮多 `tool_calls` 并发；browser/shell 自动加锁
- **结构化 Planner 输出**：`PlannerPlanSchema` Pydantic 严格校验
- **向量记忆**：`api/config.yaml` 中 `memory.vector_enabled: true` 时启用 pgvector 混合召回
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

生产环境需在 `.env` 设置强随机 `API_KEY_SECRET`（`openssl rand -hex 32`）。`llm_models.api_key_encryption` 标识存储格式：

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
