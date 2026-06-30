# MyManus - 企业级通用 AI Agent 系统

<div align="center">

**完全私有化部署 · A2A + MCP 协议集成 · 沙箱隔离执行 · 企业级高可用架构**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16-black.svg)](https://nextjs.org/)
[![Docker](https://img.shields.io/badge/docker-compose-ready-blue.svg)](https://docs.docker.com/compose/)

</div>

---

## 📋 目录

- [项目简介](#-项目简介)
- [核心特性](#-核心特性)
- [技术架构](#-技术架构)
- [快速开始](#-快速开始)
- [配置说明](#-配置说明)
- [生产环境部署](#-生产环境部署)
- [安全加固](#-安全加固)
- [监控与运维](#-监控与运维)
- [故障排查](#-故障排查)
- [开发指南](#-开发指南)
- [许可证](#-许可证)

---

## 🎯 项目简介

MyManus 是一个**企业级通用 AI Agent 系统**，支持完全私有化部署，采用现代化的微服务架构设计。系统通过 **A2A (Agent-to-Agent)** 和 **MCP (Model Context Protocol)** 协议实现智能体与工具的无缝集成，并在隔离的沙箱环境中安全执行各类任务。

### 应用场景

- 🔍 **信息收集与分析**：自动化数据采集、事实核查、深度研究报告生成
- 📊 **数据处理与可视化**：多源数据整合、统计分析、图表生成
- ✍️ **内容创作**：多章节文章撰写、技术文档生成、营销文案创作
- 💻 **编程辅助**：代码生成、调试、测试、自动化脚本编写
- 🌐 **网络操作**：网页浏览、表单填写、数据抓取、在线工具调用
- 🔧 **系统集成**：通过 MCP/A2A 协议连接企业内部系统和第三方服务

### 产品模块

除 Agent 对话主线外，系统还包含以下内置模块：

| 模块 | 入口 | 说明 |
|------|------|------|
| **Agent 对话** | `/`、`/sessions/[id]` | 多轮任务、工具调用、VNC、记忆与检查点 |
| **代码知识库** | `/codebase` | ZIP/Git 导入、符号检索、架构图、Ask/Agent 改码 |
| **应用市场** | `/marketplace` | 16+ 轻应用（翻译、营养分析、文档转换、运势等） |
| **自定义问卷** | `/marketplace` → 问卷；公开页 `/q/[slug]` | 创建、发布、填报与统计 |
| **派对房间** | `/marketplace` → 派对房间；直链 `/room/[code]` | 骰子、真心话大冒险、SSE 实时同步 |
| **人格测试分享** | `/share/test` | MBTI 等 6 套测试结果的公开分享页 |

> `.env.example` 已预填 `API_KEY_SECRET` 开箱即用；生产建议自行覆盖。可通过 `config.yaml` 的 `server.cors_origins` 限制跨域来源；公开接口默认启用基础限流。

## ✨ 核心特性

### 🏗️ 企业级架构
- **微服务设计**：前后端分离，API 与 Agent Worker **独立镜像/进程**（`manus-api` / `manus-worker`），支持水平扩展
- **任务解耦**：Redis Streams 消费组 + 独立 Worker 池执行 Agent，API 无状态处理 SSE 与事件分页重放
- **角色容器**：`ApiContainer` / `WorkerContainer` 共享 `BaseContainer`，职责边界见 [`docs/architecture.md`](docs/architecture.md)
- **高可用性**：健康检查、自动重启、服务依赖管理、迁移 init job
- **数据库迁移**：Alembic 独立 migrate job，API 启动时仅校验 schema 版本
- **分布式缓存**：Redis 支持任务 dispatch、实时事件流与会话状态

### 🤖 智能体能力
- **多轮对话**：Planner → ReAct 状态机，支持复杂任务迭代规划与执行
- **Token 级流式**：LLM delta 流式输出（content / reasoning / tool args），UI 逐字渲染
- **并行工具调用**：单轮多工具并发执行，browser/shell 等有状态工具自动串行加锁
- **任务编排**：自动分解复杂任务为可执行步骤，Planner 输出 Pydantic 严格校验
- **模型管理**：支持 OpenAI / Ollama / Azure / Anthropic / Gemini 等 Provider
- **Skill 模板**：内置编程、研究、数据分析、写作等模板，可自定义工具白名单
- **上下文管理**：长期记忆（时间衰减 + pgvector 向量混合召回）、会话内存压缩

### 🔌 协议集成
- **MCP (Model Context Protocol)**：
  - 支持 `stdio`、`sse`、`streamable_http` 多种传输协议
  - 动态加载外部工具（地图、搜索、数据分析等）
  - 工具结果缓存，优化执行效率
  
- **A2A (Agent-to-Agent)**：
  - 远程 Agent 发现与调用
  - Agent Card 自动注册
  - 支持流式与非流式通信

### 🛡️ 沙箱隔离
- **完整 Linux 环境**：Ubuntu 22.04 + Python 3.10 + Node.js 24
- **浏览器自动化**：Chromium + Playwright，支持网页交互与截图
- **虚拟显示**：Xvfb + x11vnc + websockify，提供 VNC 远程桌面
- **进程管理**：Supervisor 确保关键服务高可用
- **超时控制**：自动清理闲置沙箱，防止资源泄露

### 💾 数据存储
- **PostgreSQL 16 + pgvector**：关系型数据 + 向量 embedding（长期记忆语义召回）
- **Redis 7**：任务 dispatch 队列、Redis Streams 实时事件管道、缓存
- **追加式事件存储**：`session_events` 按 `(session_id, seq)` 持久化会话事件，支持游标分页重放
- **腾讯云 COS**：对象存储（文件上传下载、大文件托管）

### 📈 可观测性
- **Prometheus**：内置 `/api/metrics` 端点
- **OpenTelemetry**：可选 traces/metrics 导出（OTLP）
- **Agent Tracing**：Planner/ReAct 步骤级 span，LLM token 指标
- **结构化日志**：请求日志携带 `session_id` 关联字段

### 🌐 前端体验
- **现代化 UI**：Next.js 16 + React 19 + TypeScript
- **响应式设计**：适配桌面与移动设备
- **设置中心**：统一管理 LLM 模型、Skill 模板、长期记忆、A2A Agent 和 MCP 服务器
- **会话配置**：新建会话或发送消息前可选择模型与 Skill，并可在会话详情中调整
- **实时交互**：SSE 事件流 + Token 增量合并渲染，历史事件按游标分页加载；WebSocket VNC 远程桌面
- **富文本预览**：支持代码、Markdown、图片等多种格式

## 🏛️ 技术架构

### 系统架构图

```
                          ┌─────────────────────┐
                          │   Client Browser    │
                          └──────────┬──────────┘
                                     │ HTTPS/HTTP
                          ┌──────────▼──────────┐
                          │   Nginx Gateway     │  Port: 80/443
                          │  (Reverse Proxy)    │
                          └────┬───────────┬────┘
                               │           │
                    ┌──────────▼──┐   ┌───▼──────────────┐
                    │  Next.js UI │   │  FastAPI API      │
                    │  (Port 3000)│   │  (Port 8000)      │
                    └─────────────┘   │  无状态 SSE 连接   │
                                      └───────┬───────────┘
                                              │ dispatch / read streams
                        ┌─────────────────────┼─────────────────────┐
                        │                     │                     │
              ┌─────────▼────────┐  ┌────────▼───────┐  ┌─────────▼────────┐
              │ PostgreSQL 16    │  │   Redis 7      │  │ Agent Worker 池   │
              │ + pgvector       │  │  (Port 6379)   │  │ (独立进程)        │
              │                  │  │                │  │                   │
              │ • Sessions       │  │ • task:dispatch│  │ • AgentTaskRunner │
              │ • session_events │  │ • task:output  │  │ • Planner/ReAct   │
              │ • memory_entries │  │ • cancel ctrl  │  └─────────┬─────────┘
              │ • embedding      │  │                │            │
              └──────────────────┘  └────────────────┘            │
                                                        ┌─────────▼────────┐
                                                        │   Sandbox         │
                                                        │  (Internal Net)   │
                                                        │ • FastAPI :8080   │
                                                        │ • Chrome :9222    │
                                                        │ • VNC :5901       │
                                                        └───────────────────┘
                        │
              ┌─────────▼────────┐
              │  Tencent COS     │
              │  (Object Storage)│
              └──────────────────┘

  部署流程: manus-migrate (一次性) → manus-api + manus-worker
```

> 完整架构说明（进程角色、DI 容器、后台循环归属、打包规范）见 [`docs/architecture.md`](docs/architecture.md)。

### 依赖与打包规范

| 模块 | 依赖工具 | 锁文件 | Docker 镜像 |
|------|----------|--------|-------------|
| `api/` | uv | `uv.lock` | `manus-api`（target: api）、`manus-worker`（target: worker） |
| `sandbox/` | uv | `uv.lock` | `manus-sandbox` |
| `ui/` | npm | `package-lock.json` | `manus-ui` |

Python 项目统一 `pyproject.toml` + `uv sync --frozen`，不再维护 `requirements.txt`。

**Docker 构建期镜像源**（默认面向国内网络优化，可通过环境变量覆盖）：

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `PIP_INDEX_URL` | `https://mirrors.aliyun.com/pypi/simple/` | `pip install uv` 使用的 PyPI 镜像 |
| `PIP_TRUSTED_HOST` | `mirrors.aliyun.com` | pip 信任主机 |
| `PIP_DEFAULT_TIMEOUT` | `300` | pip 下载超时（秒） |
| `PIP_RETRIES` | `5` | pip 下载重试次数 |
| `UV_INDEX_URL` | `https://mirrors.aliyun.com/pypi/simple/` | `uv sync` 使用的 PyPI 镜像 |
| `UV_VERSION` | `0.11.19` | 构建期安装的 uv 版本 |
| `UV_HTTP_TIMEOUT` | `300` | `uv sync` 下载 wheel 的 HTTP 超时（秒） |
| `NPM_CONFIG_REGISTRY` | `https://registry.npmmirror.com` | sandbox / ui 的 npm 镜像 |

Compose 构建后的应用镜像统一命名为：`manus-api`、`manus-worker`、`manus-migrate`、`manus-ui`、`manus-sandbox`（不再出现 `my-manus-manus-*` 这类自动前缀名）。

> 仓库**未内置** GitHub Actions 等自动 CI/CD；镜像构建与推送需本地 `docker compose build` 或外部流水线完成。Helm Chart 仅负责 Kubernetes 部署模板。

### 技术栈详情

#### 后端服务 (api/)
| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 运行时环境 |
| FastAPI | 0.100+ | Web 框架 |
| SQLAlchemy | 2.x | ORM 框架 |
| Alembic | Latest | 数据库迁移 |
| asyncpg | Latest | PostgreSQL 异步驱动 |
| Redis | 6.4+ | 异步 Redis 客户端 |
| Docker SDK | Latest | 容器管理 |
| Playwright | Latest | 浏览器自动化 |
| MCP SDK | 1.22+ | Model Context Protocol |
| httpx | Latest | HTTP 客户端 (A2A / Anthropic / Gemini) |
| OpenTelemetry | 1.29+ | 分布式追踪与指标（可选） |
| prometheus-client | Latest | `/api/metrics` 端点 |

#### 前端服务 (ui/)
| 组件 | 版本 | 用途 |
|------|------|------|
| Next.js | 16 | React 框架 |
| React | 19 | UI 库 |
| TypeScript | 5.x | 类型系统 |
| Tailwind CSS | 4 | 样式框架 |
| Radix UI | Latest | 无头组件库 |
| noVNC | Latest | VNC 客户端 |

#### 沙箱服务 (sandbox/)
| 组件 | 版本 | 用途 |
|------|------|------|
| Ubuntu | 22.04 | 基础操作系统 |
| Chromium | Latest | 浏览器引擎 |
| Xvfb | Latest | 虚拟帧缓冲 |
| x11vnc | Latest | VNC 服务器 |
| websockify | Latest | WebSocket 代理 |
| Supervisor | Latest | 进程管理器 |

#### 基础设施
| 组件 | 版本 | 用途 |
|------|------|------|
| Nginx | Alpine | 反向代理与负载均衡 |
| PostgreSQL | 16 + pgvector | 关系型数据库 + 向量检索 |
| Redis | 7-Alpine | 任务队列与事件流 |
| Docker Compose | 2.0+ | 容器编排（含 migrate job + worker） |
| Helm | 3.x | Kubernetes 部署（见 `deploy/helm/my-manus/`） |

## 🚀 快速开始

### 前置要求

**最低配置：**
- CPU: 2 核
- 内存: 4GB
- 磁盘: 20GB 可用空间

**推荐配置：**
- CPU: 4 核+
- 内存: 8GB+
- 磁盘: 50GB+ SSD

**软件依赖：**
- Docker >= 20.10
- Docker Compose >= 2.0
- Git (可选，用于版本管理)

### 一键部署（5 分钟）

#### 1. 克隆项目

```bash
git clone https://github.com/your-org/my-manus.git
cd my-manus
```

#### 2. 配置环境变量

复制环境变量模板并编辑：

```bash
cp .env.example .env
vim .env
vim api/config.yaml
```

**必须修改的配置：**

```bash
# ==================== 腾讯云 COS 配置 ====================
COS_SECRET_ID=your_cos_secret_id_here       # 腾讯云 SecretId
COS_SECRET_KEY=your_cos_secret_key_here     # 腾讯云 SecretKey
COS_BUCKET=your_cos_bucket_here             # COS 存储桶名称
COS_REGION=ap-guangzhou                     # COS 地域

# ==================== 数据库配置 ====================
POSTGRES_USER=postgres                      # 数据库用户名
POSTGRES_PASSWORD=your_secure_password      # 数据库密码（请修改为强密码）
POSTGRES_DB=manus                           # 数据库名称

# ==================== 网络配置 ====================
NGINX_PORT=8088                             # HTTP 对外端口
NGINX_HTTPS_PORT=443                        # HTTPS 对外端口
MANUS_DOMAIN=                               # 域名（可选，HTTP 模式下留空则 server_name 为 _）
HTTPS_ENABLED=false                         # true 时启用 HTTPS（需先准备证书）
```

#### 3. 配置 AI 模型

**首次启动不会自动创建默认模型**。服务启动后，请在前端「设置中心 → 模型管理」手动添加 LLM 模型并设置默认模型，之后才能发起对话。模型配置存储在 PostgreSQL `llm_models` 表中，API Key 使用 `API_KEY_SECRET` 加密保存。

运行时行为配置（CORS、限流、沙箱、记忆、Worker 并发等）统一在 `api/config.yaml` 维护，示例：

```yaml
server:
  cors_origins: '*'
  rate_limit_enabled: true
  rate_limit_per_minute: 120

agent_config:
  max_iterations: 100
  max_retries: 3
  max_search_results: 10

sandbox:
  image: manus-sandbox
  network: manus-network
  memory_limit: 1g
  pool_enabled: true
  pool_size: 1
  ttl_minutes: 20
  idle_timeout_minutes: 10
```

**支持的 LLM 提供商：**
- DeepSeek / OpenAI / Azure OpenAI / Ollama（OpenAI 兼容接口）
- Anthropic Claude（原生 Messages API + tool use）
- Google Gemini（generateContent API）

#### 沙箱部署模式

| 场景 | 推荐配置 |
|------|----------|
| 本地开发 | `config.yaml` 中 `sandbox.address` 留空，API/Worker 通过 `docker.sock` 按会话动态创建沙箱 |
| Docker Compose 生产（推荐） | `sandbox.address` 留空 + `sandbox.driver: auto`，Worker 动态创建 `manus-sandbox-*`；固定沙箱用 `--profile fixed-sandbox` |
| Kubernetes | `deploy/helm/my-manus/` 全栈 Helm（Postgres/Redis/UI/Ingress + K8s Pod 沙箱 driver） |

#### 4. 启动服务

```bash
# 构建沙箱镜像（动态模式需镜像，但默认不启动固定 manus-sandbox 服务）
docker compose build manus-sandbox manus-api manus-worker manus-ui

# 构建并启动所有服务（首次启动需要 5-10 分钟）
# 启动顺序: postgres/redis → manus-migrate → api + worker → ui → nginx
docker compose up -d --build

# 查看服务状态（应包含 manus-migrate / manus-api / manus-worker）
docker compose ps

# 查看启动日志
docker compose logs -f
```

若构建在 `pip install uv` 或 `uv sync` 阶段超时（日志提示 `UV_HTTP_TIMEOUT current value: 30s`），请确认上述镜像源环境变量已生效，或提高 `UV_HTTP_TIMEOUT`（默认 300 秒），再单独重建：`docker compose build manus-sandbox manus-api manus-worker manus-ui`。

#### 5. 访问系统

打开浏览器访问：`http://your-server-ip:8088`

**默认端口说明：**
- `8088`: HTTP 访问端口（可通过 `NGINX_PORT` 修改）
- `443`: HTTPS 端口（`.env` 中 `HTTPS_ENABLED=true` 时生效，见[安全加固](#-安全加固)章节）

## ⚙️ 配置说明

### 环境变量详解

#### 启动引导与密钥 (.env)

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `ENV` | ❌ | development | 运行环境；生产部署设为 `production` |
| `API_KEY_SECRET` | ❌ | `.env.example` 预填示例 | 模型 API Key 加密密钥；开箱即用，生产建议覆盖 |
| `POSTGRES_*` / `SQLALCHEMY_DATABASE_URI` | ✅ | 自动派生 | PostgreSQL 连接；未设置 URI 时由 `POSTGRES_USER`/`PASSWORD`/`DB`/`HOST` 派生 |
| `REDIS_HOST` / `REDIS_PORT` | ✅ | - | Redis 连接 |
| `COS_SECRET_ID` | ✅ | - | 腾讯云 COS SecretId |
| `COS_SECRET_KEY` | ✅ | - | 腾讯云 COS SecretKey |
| `COS_BUCKET` | ✅ | - | COS 存储桶名称 |
| `COS_REGION` | ❌ | ap-guangzhou | COS 地域 |
| `EMBEDDING_API_KEY` | ❌ | - | Embedding API Key（向量记忆，开关见 config.yaml） |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | ❌ | - | Langfuse 密钥（开关见 config.yaml） |
| `NGINX_PORT` | ❌ | 8088 | Nginx HTTP 对外端口（仅 docker-compose） |
| `NGINX_HTTPS_PORT` | ❌ | 443 | Nginx HTTPS 对外端口 |
| `MANUS_DOMAIN` | ❌ | - | 绑定域名；HTTPS 启用时必填 |
| `HTTPS_ENABLED` | ❌ | false | 是否启用 HTTPS（需先准备证书） |

#### 运行时配置 (api/config.yaml)

行为类配置（CORS、限流、沙箱、记忆、Worker、Redis Stream、可观测性开关、MCP/A2A）统一在此文件维护。Docker Compose 已将 `api/config.yaml` 挂载到容器 `/app/config.yaml`，修改后重启 API/Worker 生效；Agent/MCP/A2A 段可通过设置页热更新。

##### 模型、Skill 与记忆管理

- 「设置中心 → 模型管理」支持 OpenAI、Ollama、Azure OpenAI、Anthropic、Gemini 等 Provider，并可设置默认模型。
- 「设置中心 → Skill 模板」支持维护系统提示词、可用工具、推荐模型、Agent 参数和示例问题；新建会话或会话详情页可选择 Skill。
- 「设置中心 → 长期记忆」支持全局记忆与会话记忆。任务开始时会召回相关长期记忆（时间衰减 + 可选向量语义检索）并注入 Agent 上下文。
- 开启向量记忆：在 `api/config.yaml` 设置 `memory.vector_enabled: true` 并配置 `EMBEDDING_API_KEY`；需 PostgreSQL 启用 pgvector（Docker 镜像 `pgvector/pgvector:pg16`）。
- 会话详情页可查看当前会话的 Agent 内存，并支持压缩、清空或删除单条内存消息。

##### Agent 配置

```yaml
agent_config:
  max_iterations: 100                     # 单次任务最大迭代次数
  max_retries: 3                          # 工具调用失败重试次数
  max_search_results: 10                  # 搜索引擎返回结果数
```

##### MCP 服务器配置

```yaml
mcp_config:
  mcpServers:
    amap-maps:                            # 高德地图 MCP
      transport: streamable_http          # 传输协议: stdio/sse/streamable_http
      enabled: true                       # 是否启用
      url: https://mcp.amap.com/mcp?key=xxx
      
    jina-reader:                          # Jina 阅读器 MCP
      transport: streamable_http
      enabled: true
      url: https://mcp.jina.ai/v1
      headers:
        Authorization: Bearer jina_xxx
```

**支持的 MCP 传输协议：**
- `stdio`: 标准输入输出（本地进程）
- `sse`: Server-Sent Events
- `streamable_http`: 流式 HTTP

##### A2A 服务配置

```yaml
a2a_config:
  a2a_servers: []                         # 初始为空，通过 UI 动态添加
```

**A2A 服务通过前端 UI 动态管理：**
1. 进入「设置」→「A2A Agent 配置」
2. 点击「添加远程 Agent」
3. 输入 Agent 的 Base URL（如：`https://example.com/weather-agent`）
4. 系统自动获取 Agent Card 并注册

## 🏭 生产环境部署

> 详细部署步骤、服务器初始化、备份策略见 [`DEPLOYMENT.md`](DEPLOYMENT.md)。

### 硬件要求

| 规模 | 用户数 | CPU | 内存 | 磁盘 | 备注 |
|------|--------|-----|------|------|------|
| 小型 | < 50 | 4 核 | 8GB | 100GB | 单节点部署 |
| 中型 | 50-200 | 8 核 | 16GB | 500GB | 建议读写分离 |
| 大型 | 200+ | 16 核+ | 32GB+ | 1TB+ | 需要集群部署 |

### 系统优化

#### 1. 内核参数调优

```bash
# 编辑 sysctl.conf
sudo tee -a /etc/sysctl.conf << 'EOF'
# 网络连接优化
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535

# 内存管理
vm.swappiness = 10
vm.overcommit_memory = 1

# 文件句柄
fs.file-max = 1000000
EOF

# 应用配置
sudo sysctl -p

# 设置文件描述符限制
sudo tee -a /etc/security/limits.conf << 'EOF'
* soft nofile 65536
* hard nofile 65536
EOF
```

#### 2. 宿主机内存与 Swap

16GB 单机应先右配容器配额，再保留小 swap 作 OOM 兜底（勿在超配时直接 `swapoff -a`）：

```bash
sudo bash deploy/scripts/host-tune.sh
bash deploy/scripts/verify-host-health.sh after
```

#### 3. 数据库性能调优

PostgreSQL 参数已内置于 `docker-compose.yml`（匹配 1GB 容器：`shared_buffers=256MB`、`effective_cache_size=768MB`、`work_mem=8MB`）。修改 compose 后执行：

```bash
docker compose up -d manus-postgres
```

架构演进见 [docs/architecture-evolution.md](docs/architecture-evolution.md)。

### 高可用部署

仓库当前仅内置单节点 `docker-compose.yml` 与 Helm Chart。高可用演进（PostgreSQL/Redis 外置、API/Worker HPA、沙箱外置）以 [`docs/architecture-evolution.md`](docs/architecture-evolution.md) 为准，避免在 README 中维护未落地的 compose 片段。

### 负载均衡

对于高并发场景，可在 Nginx 前增加负载均衡器：

```nginx
upstream manus_backend {
    least_conn;  # 最少连接算法
    server backend1.example.com weight=5;
    server backend2.example.com weight=3;
    server backend3.example.com backup;
}
```

### 备份策略

#### 1. 数据库备份

数据库备份策略以 [`DEPLOYMENT.md`](DEPLOYMENT.md) 为准。仓库未内置备份脚本，生产环境请将 `pg_dump`、保留周期与远端同步封装为自己的运维脚本或外部备份任务。

#### 2. 文件备份（COS 跨区域复制）

在腾讯云 COS 控制台配置跨区域复制规则，实现异地容灾。

#### 3. 配置备份

```bash
# 备份配置文件
tar czf /opt/backups/config_$(date +%Y%m%d).tar.gz \
  .env \
  api/config.yaml
```

## 🔒 安全加固

### 1. HTTPS 配置

Nginx 配置由 `.env` 驱动，容器启动时自动生成，无需手动编辑 `nginx/conf.d/`。

#### 启用 HTTPS

```bash
# 1. 自行准备证书（示例：Let's Encrypt）
sudo apt install -y certbot
docker compose stop manus-nginx
sudo certbot certonly --standalone -d your-domain.com

# 2. 编辑 .env
MANUS_DOMAIN=your-domain.com
HTTPS_ENABLED=true
NGINX_PORT=8088
NGINX_HTTPS_PORT=443

# 3. 重启 Nginx
docker compose up -d manus-nginx
```

证书默认路径：`/etc/letsencrypt/live/${MANUS_DOMAIN}/fullchain.pem` 与 `privkey.pem`。  
详细步骤见 [HTTPS_DOMAIN_SETUP.md](HTTPS_DOMAIN_SETUP.md)。

### 2. 网络安全

#### 防火墙配置

```bash
# UFW 防火墙（Ubuntu）
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 8088/tcp    # HTTP
sudo ufw allow 443/tcp     # HTTPS
sudo ufw enable

# 或使用 iptables
sudo iptables -A INPUT -p tcp --dport 8088 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -A INPUT -j DROP
```

#### Docker 网络隔离

当前架构已实现网络隔离：
- 仅 Nginx 暴露对外端口
- 其他服务仅在内部网络 `manus-network` 中通信
- 数据库和 Redis 不对外开放

### 3. 认证与授权

#### 生产密钥

- `.env.example` 已预填 `API_KEY_SECRET` 示例值，开箱即用；生产建议用 `openssl rand -hex 32` 覆盖
- LLM 模型 API Key 使用 `API_KEY_SECRET` 以 `fernet_v1` 格式加密存储在 PostgreSQL `llm_models` 表
- 历史明文数据标记为 `legacy_plaintext`；`manus-migrate` 会在 Alembic 后自动加密，无需额外命令

#### CORS 配置

生产环境在 `api/config.yaml` 中限制来源：

```yaml
server:
  cors_origins: https://your-domain.com
```

### 4. 敏感信息管理

#### 环境变量加密

```bash
# 使用 Docker Secrets（Swarm 模式）
echo "your_secret" | docker secret create postgres_password -

# 或使用 HashiCorp Vault 集中管理密钥
```

#### 配置文件权限

```bash
# 限制配置文件访问权限
chmod 600 .env
chmod 600 api/config.yaml
chown root:root .env api/config.yaml
```

### 5. 沙箱安全

沙箱已实施以下安全措施：
- ✅ 容器隔离（Docker）
- ✅ 无特权模式运行
- ✅ 网络命名空间隔离
- ✅ 自动超时销毁（可配置）
- ⚠️ 建议：启用 seccomp 和 AppArmor 配置文件

```yaml
# docker-compose.yml - 增强沙箱安全
manus-sandbox:
  security_opt:
    - no-new-privileges:true
  cap_drop:
    - ALL
  cap_add:
    - NET_BIND_SERVICE
```

### 6. 日志审计

```bash
# 查看访问日志
docker compose logs -f manus-nginx

# 分析异常请求
docker compose logs manus-api | grep -i "error\|exception"

# 集中日志管理（推荐 ELK Stack 或 Loki）
# 配置 Filebeat 收集容器日志
```

## 📊 监控与运维

### 服务健康检查

系统已配置自动健康检查：

```yaml
# docker-compose.yml 示例
healthcheck:
  test: ["CMD", "curl", "-f", "http://127.0.0.1:8000/api/status"]
  interval: 15s
  timeout: 10s
  retries: 5
  start_period: 20s
```

**查看健康状态：**

```bash
docker inspect --format='{{.State.Health.Status}}' manus-api
docker inspect --format='{{.State.Health.Status}}' manus-postgres
docker inspect --format='{{.State.Health.Status}}' manus-redis
```

### 监控指标

#### 1. 内置 Prometheus 端点

API 服务暴露 `/api/metrics`，可直接被 Prometheus scrape：

```bash
curl http://localhost:8088/api/metrics
```

#### 2. OpenTelemetry（可选）

```yaml
# api/config.yaml 中启用
observability:
  otel_enabled: true
  otel_service_name: my-manus-api
  otel_exporter_endpoint: http://otel-collector:4317
```

#### 3. 容器资源监控

```bash
# 实时资源使用
docker stats

# 查看 API / Worker / 数据库
docker stats manus-api manus-worker manus-postgres manus-redis
```

#### 4. Prometheus + Grafana（推荐扩展）

仓库未内置 Prometheus/Grafana compose 文件。接入时请在外部监控栈中 scrape API 的 `/api/metrics`，或在 Helm/Kubernetes 环境使用集群已有的 ServiceMonitor/OTel Collector。

**监控指标：**
- CPU/内存使用率（API / Worker 分离观测）
- 请求 QPS 与延迟
- Agent 步骤计数（`agent_steps_total`）
- LLM Token 用量（`llm_tokens_total`）
- 数据库连接数 / Redis 命中率
- 沙箱活跃数量

### 日志管理

#### 查看日志

```bash
# 所有服务日志
docker compose logs -f

# 特定服务日志
docker compose logs -f manus-api
docker compose logs -f manus-nginx

# 最近 100 行
docker compose logs --tail=100 manus-api

# 带时间戳
docker compose logs -t manus-api
```

#### 日志轮转

```bash
# 配置 Docker 日志轮转
# /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}

# 重启 Docker
sudo systemctl restart docker
```

#### 集中日志（生产环境推荐）

使用 ELK Stack 或 Loki 进行日志聚合：

```yaml
services:
  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"

  promtail:
    image: grafana/promtail:latest
    volumes:
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - ./promtail-config.yml:/etc/promtail/config.yml
```

### 性能优化

#### 1. 数据库优化

```sql
-- 查看慢查询
SELECT query, mean_time, calls
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;

-- 添加索引（示例）
CREATE INDEX idx_sessions_created_at ON sessions(created_at);
CREATE INDEX idx_files_session_id ON files(session_id);

-- 分析表统计信息
ANALYZE sessions;
ANALYZE files;
```

#### 2. Redis 优化

已在 `docker-compose.yml` 中配置：
- 最大内存：256MB
- 淘汰策略：allkeys-lru
- AOF 持久化：开启

根据实际负载调整：

```bash
# 查看 Redis 内存使用
docker exec manus-redis redis-cli INFO memory

# 查看命中率
docker exec manus-redis redis-cli INFO stats | grep hit
```

#### 3. Nginx 优化

```nginx
# nginx/nginx.conf
worker_processes auto;
worker_rlimit_nofile 65535;

events {
    worker_connections 4096;
    use epoll;
    multi_accept on;
}

http {
    # 开启 gzip 压缩
    gzip on;
    gzip_types text/plain text/css application/json application/javascript;
    gzip_min_length 1000;
    
    # 缓存静态资源
    location ~* \.(jpg|jpeg|png|gif|ico|css|js)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

### 常用运维命令

```bash
# ==================== 服务管理 ====================

# 启动所有服务
docker compose up -d

# 停止所有服务
docker compose down

# 重启特定服务
docker compose restart manus-api
docker compose restart manus-worker

# 重建服务（代码更新后）
docker compose up -d --build manus-api manus-worker

# 查看 Worker 日志
docker compose logs -f manus-worker

# 查看服务状态
docker compose ps

# ==================== 日志查看 ====================

# 实时日志
docker compose logs -f

# 最近 100 行日志
docker compose logs --tail=100 manus-api

# 导出日志
docker compose logs manus-api > api_logs.txt

# ==================== 数据库操作 ====================

# 手动执行数据库迁移（通常由 manus-migrate 自动完成）
docker compose run --rm manus-migrate
# 或本地: cd api && ./migrate.sh（委托 python -m app.migrate）

# 进入 PostgreSQL
docker exec -it manus-postgres psql -U postgres -d manus

# 检查 pgvector 扩展
docker exec manus-postgres psql -U postgres -d manus -c "SELECT extname FROM pg_extension WHERE extname='vector';"

# 备份数据库
docker exec manus-postgres pg_dump -U postgres manus > backup.sql

# 恢复数据库
docker exec -i manus-postgres psql -U postgres manus < backup.sql

# ==================== 清理与维护 ====================

# 清理未使用的镜像
docker image prune -a

# 清理未使用的卷（⚠️ 谨慎操作，会删除数据）
docker volume prune

# 查看磁盘使用
docker system df

# ==================== 沙箱管理 ====================

# 查看沙箱容器（固定沙箱 + 动态创建的 manus-sandbox-* 容器）
docker ps -a --filter "name=manus-sandbox"

# 手动清理闲置动态沙箱（Worker 也会按 cleanup_interval_seconds 自动执行）
docker exec manus-worker python -c "from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox; import asyncio; print(asyncio.run(DockerSandbox.cleanup_orphaned_containers()))"
```

## 🛠️ 开发指南

### 本地开发（API + Worker 分离）

```bash
cd api

# 1. 安装依赖
uv sync --frozen
playwright install

# 2. 启动基础设施（PostgreSQL + Redis）
docker compose up -d manus-postgres manus-redis

# 3. 执行数据库迁移（完整入口：Alembic + 数据迁移/配置种子）
./migrate.sh

# 4. 启动 API（终端 1）
./run.sh

# 5. 启动 Agent Worker（终端 2）
./worker.sh
```

API 负责 HTTP/SSE 连接；Worker 从 Redis `task:dispatch` 消费组领取任务并执行 Agent 循环。两者共享同一 `.env` 与 PostgreSQL/Redis。

### Kubernetes / Helm 部署

```bash
# 见 deploy/helm/my-manus/
helm upgrade --install my-manus ./deploy/helm/my-manus \
  --set replicaCount.api=2 \
  --set replicaCount.worker=2 \
  --set autoscaling.api.enabled=true
```

Chart 包含 API（`manus-api`）/ Worker（`manus-worker`）独立 Deployment、HPA、migrate initContainer。

### 新增数据库迁移

```bash
cd api
alembic revision --autogenerate -m "描述"
./migrate.sh
```

## 📄 许可证

MIT License
