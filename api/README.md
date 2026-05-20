# MyManus API 服务

基于 FastAPI 构建的后端 API 服务，提供会话管理、AI Agent 调度、模型管理、Skill 模板、长期记忆、文件处理、沙箱管理等核心功能。

## 技术栈

- Python 3.12+
- FastAPI + Uvicorn
- SQLAlchemy (asyncpg) + Alembic
- Redis (异步客户端)
- Docker SDK (沙箱管理)
- Playwright (浏览器自动化)
- WebSocket (VNC 代理转发)

## 项目结构

```
api/
├── app/
│   ├── application/       # 应用层（业务服务编排）
│   ├── domain/            # 领域层（核心业务逻辑）
│   ├── infrastructure/    # 基础设施层（外部服务集成）
│   │   ├── external/      # 沙箱、浏览器等外部服务
│   │   ├── storage/       # PostgreSQL、Redis、COS 存储
│   │   ├── security/      # API Key 加密等安全能力
│   │   └── models/        # ORM 模型
│   ├── interfaces/        # 接口层（API 端点）
│   │   ├── endpoints/     # 路由定义
│   │   └── schemas/       # 请求/响应模型
│   └── main.py            # 应用入口
├── alembic/               # 数据库迁移
├── core/
│   └── config.py          # 配置管理（Pydantic Settings）
├── .env                   # 环境变量
├── config.yaml            # 应用配置（LLM、MCP、A2A）
├── Dockerfile
├── requirements.txt
└── run.sh                 # 启动脚本
```

## API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 健康检查 |
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
| PATCH | `/api/sessions/{id}` | 更新会话模型或 Skill 配置 |
| POST | `/api/sessions/{id}/chat` | SSE 流式对话 |
| GET | `/api/sessions/{id}/memory` | 获取会话 Agent 内存 |
| POST | `/api/sessions/{id}/memory/compact` | 压缩会话 Agent 内存 |
| POST | `/api/sessions/{id}/memory/clear` | 清空会话 Agent 内存 |
| DELETE | `/api/sessions/{id}/memory/{agent_name}/messages/{index}` | 删除指定会话内存消息 |
| WS | `/api/sessions/{id}/vnc` | VNC WebSocket 代理 |

## 模型、Skill 与记忆

- 首次启动时，如果数据库没有任何模型，会从 `config.yaml` 的 `llm_config` 初始化一个默认模型；之后模型配置通过 `/api/llm-models` 管理。
- 模型 API Key 会加密存储，列表和详情接口默认返回掩码值，编辑模型时可留空表示不更新密钥。
- 内置 Skill 会在启动时自动种子化，包括编程助手、研究分析、数据分析和内容写作；内置 Skill 不可删除，但可以禁用或编辑。
- 长期记忆支持 `global` 与 `session` 两种作用域，任务开始时会召回相关记忆并注入 Agent 上下文。
- 会话支持 `model_id` 与 `skill_id`，可在创建会话、发起聊天或 `PATCH /api/sessions/{id}` 时指定。

## 本地开发

### 环境准备

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 激活虚拟环境
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 3. 安装依赖
pip install uv
uv pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install
```

### 配置环境变量

修改 `.env` 文件，将数据库和 Redis 地址改为 `localhost`：

```bash
SQLALCHEMY_DATABASE_URI=postgresql+asyncpg://postgres:postgres@localhost:5432/manus
REDIS_HOST=localhost
REDIS_PORT=6379
SANDBOX_ADDRESS=         # 留空则动态创建沙箱容器
```

### 启动服务

```bash
# 启动开发服务器
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后访问 `http://localhost:8000/docs` 查看 API 文档。

### 数据库迁移

```bash
# 生成迁移脚本
alembic revision --autogenerate -m "描述"

# 执行迁移
alembic upgrade head

# 回滚
alembic downgrade -1
```

## Docker 部署

API 服务通过根目录的 `docker-compose.yml` 统一部署，无需单独构建。环境变量由根目录 `.env` 文件提供。
