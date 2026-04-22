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

## ✨ 核心特性

### 🏗️ 企业级架构
- **微服务设计**：前后端分离，独立容器化部署，支持水平扩展
- **高可用性**：健康检查、自动重启、服务依赖管理
- **数据库迁移**：Alembic 自动化数据库版本管理与迁移
- **分布式缓存**：Redis 支持会话状态缓存与消息队列

### 🤖 智能体能力
- **多轮对话**：支持复杂任务的迭代规划与执行
- **流式响应**：SSE (Server-Sent Events) 实时输出执行过程
- **任务编排**：自动分解复杂任务为可执行步骤
- **上下文管理**：长期记忆与短期记忆结合

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
- **PostgreSQL 16**：关系型数据存储（会话、文件元数据、应用配置）
- **Redis 7**：高速缓存与消息队列（Redis Stream）
- **腾讯云 COS**：对象存储（文件上传下载、大文件托管）

### 🌐 前端体验
- **现代化 UI**：Next.js 16 + React 19 + TypeScript
- **响应式设计**：适配桌面与移动设备
- **实时交互**：WebSocket VNC 代理，远程桌面无缝集成
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
                    ┌──────────▼──┐   ┌───▼──────────┐
                    │  Next.js UI │   │  FastAPI      │
                    │  (Port 3000)│   │  (Port 8000)  │
                    └─────────────┘   └───────┬───────┘
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        │                     │                     │
              ┌─────────▼────────┐  ┌────────▼───────┐  ┌─────────▼────────┐
              │   PostgreSQL 16  │  │   Redis 7      │  │   Sandbox         │
              │  (Port 5432)     │  │  (Port 6379)   │  │  (Internal Net)   │
              │                  │  │                │  │                   │
              │ • Sessions       │  │ • Cache        │  │ • FastAPI :8080   │
              │ • Files Metadata │  │ • Message Queue│  │ • Chrome :9222    │
              │ • App Config     │  │ • Rate Limit   │  │ • VNC :5901       │
              └──────────────────┘  └────────────────┘  └───────────────────┘
                        │
              ┌─────────▼────────┐
              │  Tencent COS     │
              │  (Object Storage)│
              │ • File Storage   │
              └──────────────────┘
```

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
| httpx | Latest | HTTP 客户端 (A2A) |

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
| PostgreSQL | 16-Alpine | 关系型数据库 |
| Redis | 7-Alpine | 缓存与消息队列 |
| Docker Compose | 2.0+ | 容器编排 |

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
NGINX_PORT=8088                             # 对外访问端口
```

#### 3. 配置 AI 模型

编辑 `api/config.yaml`，配置 LLM 提供商：

```yaml
llm_config:
  base_url: https://api.deepseek.com/       # LLM API 地址
  api_key: sk-your_api_key_here             # API Key
  model_name: deepseek-reasoner             # 模型名称
  temperature: 0.7                          # 温度参数 (0-1)
  max_tokens: 8192                          # 最大输出 Token 数

agent_config:
  max_iterations: 100                       # 最大迭代次数
  max_retries: 3                            # 失败重试次数
  max_search_results: 10                    # 搜索结果数量
```

**支持的 LLM 提供商：**
- DeepSeek
- OpenAI
- Anthropic Claude
- Azure OpenAI
- 本地部署模型 (Ollama, vLLM 等)

#### 4. 启动服务

```bash
# 构建并启动所有服务（首次启动需要 5-10 分钟）
docker compose up -d --build

# 查看服务状态
docker compose ps

# 查看启动日志
docker compose logs -f
```

#### 5. 访问系统

打开浏览器访问：`http://your-server-ip:8088`

**默认端口说明：**
- `8088`: HTTP 访问端口（可通过 `NGINX_PORT` 环境变量修改）
- `443`: HTTPS 端口（需配置 SSL 证书，见[安全加固](#-安全加固)章节）

## ⚙️ 配置说明

### 环境变量详解

#### 核心配置 (.env)

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `COS_SECRET_ID` | ✅ | - | 腾讯云 COS SecretId |
| `COS_SECRET_KEY` | ✅ | - | 腾讯云 COS SecretKey |
| `COS_BUCKET` | ✅ | - | COS 存储桶名称 |
| `COS_REGION` | ❌ | ap-guangzhou | COS 地域 |
| `POSTGRES_USER` | ❌ | postgres | PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | ❌ | postgres | PostgreSQL 密码 |
| `POSTGRES_DB` | ❌ | manus | PostgreSQL 数据库名 |
| `NGINX_PORT` | ❌ | 8088 | Nginx 对外端口 |
| `REDIS_MAX_MEMORY` | ❌ | 256mb | Redis 最大内存 |

#### 应用配置 (api/config.yaml)

##### LLM 配置

```yaml
llm_config:
  base_url: https://api.deepseek.com/     # API 基础 URL
  api_key: sk-xxx                         # API Key
  model_name: deepseek-reasoner           # 模型名称
  temperature: 0.7                        # 温度 (0-1，越高越随机)
  max_tokens: 8192                        # 最大输出 Token 数
```

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

#### 2. 禁用 Swap（推荐）

```bash
# 临时禁用
sudo swapoff -a

# 永久禁用
sudo sed -i '/ swap / s/^/#/' /etc/fstab
```

#### 3. 数据库性能调优

根据服务器内存调整 PostgreSQL 配置：

```bash
# 16GB 内存示例
docker exec manus-postgres bash -c "cat >> /var/lib/postgresql/data/postgresql.conf << 'EOF'
shared_buffers = 4GB                    # 内存的 25%
effective_cache_size = 12GB             # 内存的 75%
work_mem = 64MB                         # 单个查询工作内存
maintenance_work_mem = 512MB            # 维护操作内存
max_connections = 100                   # 最大连接数
checkpoint_completion_target = 0.9      # Checkpoint 分散写入
wal_buffers = 16MB                      # WAL 缓冲区
EOF"

# 重启 PostgreSQL
docker compose restart manus-postgres
```

### 高可用部署

#### 主从复制（PostgreSQL）

```yaml
# docker-compose.ha.yml
services:
  manus-postgres-master:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_master_data:/var/lib/postgresql/data
    command: >
      postgres
      -c wal_level=replica
      -c max_wal_senders=3
      -c max_replication_slots=3

  manus-postgres-slave:
    image: postgres:16-alpine
    depends_on:
      - manus-postgres-master
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    command: >
      bash -c "
      pg_basebackup -h manus-postgres-master -U ${POSTGRES_USER} -D /var/lib/postgresql/data -Fp -Xs -P -R &&
      exec postgres
      "
```

#### Redis Sentinel

```yaml
services:
  manus-redis-sentinel:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./redis/sentinel.conf:/etc/redis/sentinel.conf
```

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

```bash
#!/bin/bash
# backup_postgres.sh

BACKUP_DIR="/opt/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

mkdir -p $BACKUP_DIR

# 全量备份
docker exec manus-postgres pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} | \
  gzip > ${BACKUP_DIR}/manus_${DATE}.sql.gz

# 删除过期备份
find $BACKUP_DIR -name "manus_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

# 同步到远程存储（可选）
# aws s3 cp ${BACKUP_DIR}/manus_${DATE}.sql.gz s3://your-backup-bucket/
```

**定时任务：**

```bash
# 每天凌晨 2 点备份
crontab -e
0 2 * * * /opt/scripts/backup_postgres.sh >> /var/log/backup.log 2>&1
```

#### 2. 文件备份（COS 跨区域复制）

在腾讯云 COS 控制台配置跨区域复制规则，实现异地容灾。

#### 3. 配置备份

```bash
# 备份配置文件
tar czf /opt/backups/config_$(date +%Y%m%d).tar.gz \
  .env \
  api/config.yaml \
  nginx/conf.d/default.conf
```

## 🔒 安全加固

### 1. HTTPS 配置

#### 使用 Let's Encrypt（免费）

```bash
# 安装 Certbot
sudo apt install -y certbot

# 获取证书（ standalone 模式，需临时停止 Nginx）
docker compose down manus-nginx
sudo certbot certonly --standalone -d your-domain.com

# 配置 Nginx
mkdir -p nginx/ssl
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem nginx/ssl/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem nginx/ssl/

# 取消 nginx/conf.d/default.conf 中的 SSL 注释
# 取消 docker-compose.yml 中 443 端口映射注释

# 重启服务
docker compose up -d manus-nginx

# 配置自动续期
sudo crontab -e
# 添加：0 3 1 * * certbot renew --quiet && docker compose exec manus-nginx nginx -s reload
```

#### 使用商业证书

```bash
# 将证书文件放入 nginx/ssl/ 目录
# - fullchain.pem（证书链）
# - privkey.pem（私钥）

# 修改权限
chmod 600 nginx/ssl/*.pem

# 重启 Nginx
docker compose restart manus-nginx
```

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

#### API Key 保护（待实现）

建议在 production 环境中添加 API 认证：

```python
# api/app/interfaces/middleware/auth.py
from fastapi import Request, HTTPException

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
```

#### CORS 配置

当前配置允许所有来源（开发环境），生产环境应限制：

```python
# api/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-domain.com"],  # 限制域名
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
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

#### 1. 容器资源监控

```bash
# 实时资源使用
docker stats

# 查看特定容器
docker stats manus-api manus-postgres manus-redis
```

#### 2. Prometheus + Grafana（推荐）

```yaml
# docker-compose.monitoring.yml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    volumes:
      - grafana_data:/var/lib/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

**监控指标：**
- CPU/内存使用率
- 请求 QPS 与延迟
- 数据库连接数
- Redis 命中率
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

# 重建服务（代码更新后）
docker compose up -d --build manus-api

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

# 进入 PostgreSQL
docker exec -it manus-postgres psql -U postgres -d manus

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

# 查看沙箱状态
docker exec manus-api python -c "from app.interfaces.service_dependencies import get_sandbox_service; import asyncio; print(asyncio.run(get_sandbox_service().get_status()))"

# 手动清理沙箱
docker exec manus-api python -c "from app.interfaces.service_dependencies import get_sandbox_service; import asyncio; asyncio.run(get_sandbox_service().cleanup())"
```
