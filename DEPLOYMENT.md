# MyManus 生产环境部署指南

## 📋 服务器信息

| 项目 | 配置 |
|------|------|
| **实例ID** | lhins-afu6m4i6 |
| **实例名称** | Ubuntu-jkDL |
| **操作系统** | Ubuntu 24.04 LTS |
| **CPU/内存** | 8核 / 16GB |
| **系统盘** | 270GB SSD |
| **带宽** | 18Mbps |

---

## 🚀 快速部署（5分钟）

### 1. 服务器初始化

```bash
# SSH登录服务器
ssh root@YOUR_SERVER_IP

# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装必要工具
sudo apt install -y curl wget git vim ufw
```

### 2. 安装 Docker 环境

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun

# 启动 Docker
sudo systemctl enable docker
sudo systemctl start docker

# 安装 Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 验证安装
docker --version
docker-compose --version

# 将当前用户加入 docker 组（避免每次使用 sudo）
sudo usermod -aG docker $USER
newgrp docker
```

### 3. 部署应用

```bash
# 克隆代码
cd /opt
git clone <YOUR_REPOSITORY_URL> my-manus
cd my-manus

# 创建环境变量文件
cp .env.example .env

# 编辑配置文件（见下方配置说明）
vim .env
vim api/config.yaml

# 构建并启动服务
docker compose up -d --build

# 查看服务状态（含 manus-migrate / manus-api / manus-worker）
docker compose ps
docker compose logs -f
```

> **服务启动顺序**：`manus-postgres` + `manus-redis` → `manus-migrate`（Alembic + LLM Key 迁移）→ `manus-api` + `manus-worker` → `manus-ui` → `manus-nginx`

> **Agent Worker 必须运行**：若 `manus-worker` 未启动，对话请求会写入队列但 Agent 不会执行。可通过 `docker compose logs -f manus-worker` 排查。

### 3.1 Docker 构建期镜像源（可选）

`docker-compose.yml` 已为 Python / npm 服务注入统一 build args，默认使用阿里云 PyPI 与 npmmirror，避免 `files.pythonhosted.org` 下载超时。企业内网可在 `.env` 或 shell 中覆盖：

```bash
# 示例：使用私有 PyPI 代理
export PIP_INDEX_URL=https://pypi.mycompany.internal/simple/
export PIP_TRUSTED_HOST=pypi.mycompany.internal
export UV_INDEX_URL=https://pypi.mycompany.internal/simple/
export NPM_CONFIG_REGISTRY=https://npm.mycompany.internal/

docker compose build
docker compose up -d
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PIP_INDEX_URL` | 阿里云 PyPI | `pip install uv` |
| `UV_INDEX_URL` | 阿里云 PyPI | `uv sync --frozen` |
| `UV_VERSION` | `0.11.19` | 固定构建期 uv 版本 |
| `NPM_CONFIG_REGISTRY` | npmmirror | sandbox / ui 的 npm |

> **CI/CD 说明**：本仓库无自动 CI workflow。生产发布流程为：本地或外部流水线 `docker compose build` →（可选）推送到镜像仓库 → `docker compose up` 或 Helm 部署 API/Worker。

---

## ⚙️ 核心配置

### 启动引导与密钥 (.env)

`.env` 仅保留进程启动前必须可用的连接串与密钥；行为类配置见 `api/config.yaml`。

```bash
# ==================== 必填最小集 ====================
ENV=production
LOG_LEVEL=INFO
# .env.example 已预填开箱即用示例；生产建议覆盖: openssl rand -hex 32
API_KEY_SECRET=<from .env.example>

# 数据库（未设置 SQLALCHEMY_DATABASE_URI 时由 POSTGRES_* 自动派生）
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<STRONG_PASSWORD>
POSTGRES_DB=manus
POSTGRES_HOST=manus-postgres

# Redis（未设置密码时留空）
REDIS_HOST=manus-redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# 腾讯云 COS
COS_SECRET_ID=<YOUR_COS_SECRET_ID>
COS_SECRET_KEY=<YOUR_COS_SECRET_KEY>
COS_REGION=ap-guangzhou
COS_BUCKET=<YOUR_BUCKET_NAME>
COS_DOMAIN=<YOUR_COS_DOMAIN>

# Nginx 端口
NGINX_PORT=8088

# ==================== 按功能启用的密钥（可选） ====================
# 向量记忆 / Langfuse：先在 api/config.yaml 启用对应开关后再填写
EMBEDDING_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

行为类配置（CORS、限流、沙箱、记忆、Worker 并发、OTEL 开关等）统一在 `api/config.yaml` 维护，不要写入 `.env`。

### 运行时配置 (api/config.yaml)

Docker Compose 将 `./api/config.yaml` 挂载到 API/Worker 容器的 `/app/config.yaml`。

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
  address: null
  image: manus-sandbox
  name_prefix: manus-sandbox
  network: manus-network
  memory_limit: 1g
  pool_enabled: true
  pool_size: 1          # 只预热 1 个；并发任务按需创建，上限见 worker.max_concurrent_tasks
  ttl_minutes: 20
  idle_timeout_minutes: 10
  cleanup_interval_seconds: 120

memory:
  vector_enabled: false
  embedding:
    provider: openai
    model: text-embedding-3-small
    base_url: https://api.openai.com/v1

observability:
  otel_enabled: false
  otel_service_name: my-manus-api

mcp_config:
  mcpServers:
    amap-maps-streamableHTTP:
      transport: streamable_http
      enabled: true
      url: https://mcp.amap.com/mcp?key=YOUR_AMAP_KEY

a2a_config:
  a2a_servers: []
```

### 模型、Skill 与记忆

- **首次启动不会自动导入默认模型**，请在前端「设置中心 → 模型管理」添加模型并设置默认项后才能发起对话。模型存储在 PostgreSQL `llm_models` 表，API Key 由 `API_KEY_SECRET` 加密。
- `llm_models.api_key_encryption` 字段标识存储格式：`legacy_plaintext`（历史明文）或 `fernet_v1`（加密存储）。`manus-migrate` 会在 Alembic 后自动加密历史明文，无需额外命令。
- 系统会自动创建内置 Skill 模板（编程助手、研究分析、数据分析、内容写作），也可在「设置中心 → Skill 模板」维护自定义模板。
- 长期记忆在「设置中心 → 长期记忆」维护，支持全局和会话两种作用域；任务开始时会自动召回相关记忆（时间衰减 + 可选 pgvector 向量混合检索）。
- 开启向量记忆需在 `config.yaml` 设置 `memory.vector_enabled: true`，并在 `.env` 配置 `EMBEDDING_API_KEY`；PostgreSQL 使用 `pgvector/pgvector:pg16` 镜像。
- 会话详情页可查看 Agent 会话内存，并支持压缩、清空或删除单条内存消息。

### 数据库迁移

迁移由 **`manus-migrate` 一次性 init job** 自动执行：先跑 Alembic schema 迁移，再加密历史明文 LLM API Key。API 启动时仅校验 schema 版本，不再在 lifespan 内跑 `alembic upgrade`。

```bash
# 正常部署：docker compose up 会自动运行 manus-migrate
docker compose up -d --build

# 手动执行迁移（版本升级或排查）
docker compose run --rm manus-migrate
# 或进入 api 容器:
docker compose exec manus-api python -m app.migrate

# 本地开发
cd api && ./migrate.sh
```

新增迁移版本包括 `memory_entries.embedding vector(1536)`（pgvector 扩展）。

---

## 🔒 安全加固

### 1. 防火墙配置

```bash
# 启用 UFW 防火墙
sudo ufw enable

# 允许 SSH
sudo ufw allow 22/tcp

# 允许应用端口
sudo ufw allow 8088/tcp

# 查看规则
sudo ufw status verbose
```

### 2. Docker 安全优化

```bash
# 限制容器资源使用
# 编辑 docker-compose.yml，为关键服务添加资源限制：

# manus-api 示例
services:
  manus-api:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 1G
```

### 3. 数据备份策略

```bash
# 创建备份脚本
cat > /opt/my-manus/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/manus"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# 备份 PostgreSQL
docker exec manus-postgres pg_dump -U postgres manus > $BACKUP_DIR/db_$DATE.sql

# 压缩备份
tar -czf $BACKUP_DIR/backup_$DATE.tar.gz -C $BACKUP_DIR db_$DATE.sql
rm $BACKUP_DIR/db_$DATE.sql

# 保留最近7天备份
find $BACKUP_DIR -name "backup_*.tar.gz" -mtime +7 -delete

echo "Backup completed: backup_$DATE.tar.gz"
EOF

chmod +x /opt/my-manus/backup.sh

# 设置定时任务（每天凌晨2点备份）
crontab -e
# 添加：0 2 * * * /opt/my-manus/backup.sh >> /var/log/manus-backup.log 2>&1
```

---

## 📊 监控与日志

### 1. 查看服务状态

```bash
# 查看所有容器状态
docker-compose ps

# 查看实时日志
docker-compose logs -f manus-api
docker-compose logs -f manus-ui
docker-compose logs -f manus-nginx

# 查看资源使用
docker stats
```

### 2. 健康检查

```bash
# API 健康检查
curl http://localhost:8088/api/status

# Prometheus 指标
curl http://localhost:8088/api/metrics

# 前端访问测试
curl -I http://localhost:8088

# 数据库连接测试
docker exec manus-postgres pg_isready -U postgres

# Worker 运行状态
docker compose logs --tail=50 manus-worker
```

### 3. 日志管理

```bash
# 配置 Docker 日志轮转
cat > /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF

# 重启 Docker
sudo systemctl restart docker
```

---

## 🔄 运维操作

### 服务管理

```bash
# 启动所有服务
cd /opt/my-manus
docker-compose up -d

# 停止所有服务
docker-compose down

# 重启单个服务
docker compose restart manus-api
docker compose restart manus-worker

# 扩展 Worker 副本（需移除 compose 中 container_name 或使用 scale profile）
# docker compose up -d --scale manus-worker=2

# 重新构建并启动
docker-compose up -d --build

# 查看服务日志
docker-compose logs -f --tail=100 manus-api
```

### 版本更新

```bash
# 拉取最新代码
cd /opt/my-manus
git pull origin main

# 重新构建镜像
docker-compose build

# 滚动更新（零停机）
docker compose up -d --build manus-api manus-worker

# 清理旧镜像
docker image prune -f
```

### 数据库维护

```bash
# 进入数据库
docker exec -it manus-postgres psql -U postgres -d manus

# 执行迁移
docker compose run --rm manus-migrate

# 备份恢复
docker exec -i manus-postgres psql -U postgres manus < backup.sql
```

### LLM API Key 迁移

常规部署/升级只需 `docker compose up -d --build`，`manus-migrate` 会自动完成历史明文加密。迁移日志只输出统计信息与 `model_id`，不会打印真实 API Key。

若升级后迁移容器未重建，可补救：

```bash
docker compose up -d --build --force-recreate manus-migrate manus-api manus-worker
```

极端情况下可单独修复：

```bash
docker compose run --rm manus-api python -m app.migrate_llm_api_keys
```

轮换 `API_KEY_SECRET` 后，已加密的模型密钥需在前端重新保存。

---

## 🛠️ 故障排查

### 常见问题

#### 1. 容器启动失败

```bash
# 查看详细日志
docker compose logs manus-api

# 检查配置文件
docker exec -it manus-api printenv API_KEY_SECRET ENV SQLALCHEMY_DATABASE_URI
docker exec -it manus-api cat /app/config.yaml

# 验证网络连接
docker network inspect manus-network
```

#### 2. 数据库连接失败

若 `manus-migrate` 报 `password authentication failed for user "postgres"`：

- `manus-postgres` 与 `manus-migrate` 现在都从 `POSTGRES_*` 派生连接串；不要只改 `POSTGRES_PASSWORD` 却保留旧的 `SQLALCHEMY_DATABASE_URI`。
- PostgreSQL 数据卷只在**首次初始化**时写入密码；之后仅改 `.env` 不会自动更新卷内密码。
- 让数据库密码与 `.env` 对齐：`ALTER USER postgres WITH PASSWORD '<POSTGRES_PASSWORD>';`
- 全新环境可 `docker compose down -v` 后重建（会删除数据库数据）。

```bash
# 检查数据库状态
docker compose logs manus-postgres

# 测试连接
docker exec manus-postgres pg_isready -U postgres -d manus

# 查看 migrate 实际使用的连接参数（URI 由 POSTGRES_* 派生）
docker compose run --rm manus-migrate python -c "from core.config import get_settings; print(get_settings().sqlalchemy_database_uri)"

# 重置密码（与 .env 中 POSTGRES_PASSWORD 保持一致）
docker exec -it manus-postgres psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'new_password';"
```

#### 3. 内存不足 / Swap 抖动

16GB 单机若内存长期 >95% 且磁盘读 IO 持续高位，多为**超额订阅 + Swap 换页**，而非 CPU 不足。

```bash
# 一键采集调优前后指标（si/so 非零 = swap 抖动）
bash deploy/scripts/verify-host-health.sh before
bash deploy/scripts/verify-host-health.sh after

# 查看内存与容器配额
free -h
swapon --show
vmstat 1 5
docker stats --no-stream
docker ps -a --filter "name=manus-sandbox-"

# 宿主机调优（4G swap 兜底 + vm.swappiness=10 + Docker 日志轮转）
sudo bash deploy/scripts/host-tune.sh

# 应用右配后的 compose 与 config（见 docker-compose.yml / api/config.yaml）
cd /opt/my-manus && docker compose up -d --build

# 清理未使用的镜像/容器（勿随意 --volumes，会删数据库卷）
docker system prune -a -f
```

**内存预算（16GB 主机，已右配）**

| 服务 | mem_limit |
|------|-----------|
| postgres | 1536m |
| api | 1g |
| worker | 1536m |
| ui | 512m |
| redis | 512m |
| nginx | 128m |
| 沙箱（1 预热 + 最多 3 按需） | 1~4g |

#### 4. Nginx 502 错误

```bash
# 检查后端服务
docker-compose ps manus-api manus-ui

# 检查 Nginx 配置
docker exec manus-nginx nginx -t

# 重载 Nginx
docker exec manus-nginx nginx -s reload
```

---

## 📈 性能优化建议

### 1. 宿主机调优（推荐首次部署后执行）

```bash
# 一键：vm.swappiness=10、4G swap 兜底、Docker 日志轮转
sudo bash deploy/scripts/host-tune.sh

# 验证（调优后 si/so 应为 0，内存 idle <80%）
bash deploy/scripts/verify-host-health.sh after
```

> **不要**在内存仍超额订阅时 `swapoff -a`：会从 swap 抖动变为 OOM kill。应先右配 `docker-compose.yml` 与 `api/config.yaml`，再保留小 swap 作兜底。

### 2. 容器与沙箱配额

已在 [docker-compose.yml](docker-compose.yml) 与 [api/config.yaml](api/config.yaml) 右配：

- 核心服务 mem_limit 合计约 5.2GB，含 `mem_reservation` 防互相挤占
- 沙箱：**1 预热 + 按需创建**，`memory_limit: 1g`，`pool_size: 1`
- 并发沙箱上限 = `worker.max_concurrent_tasks`（默认 4）

### 3. PostgreSQL 调优

Postgres 参数已内置于 `docker-compose.yml` 的 `command`（匹配 1.5GB 容器配额）：

- `shared_buffers = 384MB`
- `effective_cache_size = 1GB`
- `work_mem = 16MB`
- `maintenance_work_mem = 128MB`

修改后执行：`docker compose up -d manus-postgres`

### 4. Redis 优化

已在 docker-compose.yml 中配置：
- 最大内存：256MB
- 淘汰策略：allkeys-lru
- AOF 持久化：开启

### 5. 架构演进

单机稳定后若需水平扩展，见 [docs/architecture-evolution.md](docs/architecture-evolution.md)（DB/Redis 外置、K8s HPA、沙箱外置）。

---

## 🔐 HTTPS 配置（可选）

默认 HTTP 即可使用；需要 HTTPS 时三步启用：

```bash
# 1. 获取证书
sudo apt install -y certbot
sudo certbot certonly --standalone -d your-domain.com

# 2. 取消注释（将 your-domain.com 替换为实际域名）
#    - docker-compose.yml: manus-nginx 的 443 端口与 /etc/letsencrypt 挂载
#    - nginx/conf.d/default.conf: 顶部 SSL server 块

# 3. 重载 Nginx
docker compose up -d manus-nginx
docker compose exec manus-nginx nginx -s reload

# 证书续期（可选 cron）
# 0 3 1 * * certbot renew --quiet && docker compose exec manus-nginx nginx -s reload
```

---

## ☸️ Kubernetes / Helm 部署

Helm Chart 位于 `deploy/helm/my-manus/`，支持 API/Worker 独立 Deployment 与 HPA。完整演进路径（沙箱外置、有状态下沉、背压策略）见 [docs/architecture-evolution.md](docs/architecture-evolution.md)。

```bash
# 构建并推送镜像（api/Dockerfile 多阶段 target）
docker build --target api -t your-registry/manus-api ./api
docker build --target worker -t your-registry/manus-worker ./api
docker push your-registry/manus-api
docker push your-registry/manus-worker

helm upgrade --install my-manus ./deploy/helm/my-manus \
  --set image.api.repository=your-registry/manus-api \
  --set image.worker.repository=your-registry/manus-worker \
  --set replicaCount.api=2 \
  --set replicaCount.worker=2 \
  --set autoscaling.api.enabled=true \
  --set autoscaling.worker.enabled=true \
  --set migrate.enabled=true
```

Chart 特性：
- API Deployment 含 **migrate initContainer**（与 `manus-migrate` 等价）
- Worker Deployment 独立 HPA
- readiness/liveness 探针分离

> Chart 为骨架配置，生产需自行补全 Secret（`API_KEY_SECRET`、数据库/Redis、COS）与 `config.yaml` ConfigMap。

---

## 🆘 技术支持

- **项目文档**: README.md
- **API 文档**: http://YOUR_SERVER_IP:8088/docs
- **日志位置**: `docker-compose logs`
- **数据目录**: `/var/lib/docker/volumes`

---

**最后更新时间**: 2026-06-11
**适用版本**: MyManus v1.0  
**部署环境**: Ubuntu 24.04 LTS, 8核/16GB/270GB SSD/18Mbps
