[English](deployment.md)

# OpenCitadel 生产环境部署指南

## 📋 服务器建议

| 项目 | 配置 |
|------|------|
| **操作系统** | Ubuntu 24.04 LTS 或同等 Linux 发行版 |
| **CPU/内存** | 生产建议 8 核 / 16GB 起 |
| **系统盘** | 100GB+ SSD，按文件与日志保留周期扩容 |
| **带宽** | 按用户规模与文件上传需求评估 |

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
git clone https://github.com/OceaLong/opencitadel.git opencitadel
cd opencitadel

# 创建环境变量文件
cp .env.example .env

# 编辑配置文件（见下方配置说明）
vim .env
vim api/config.yaml

# 构建沙箱镜像（动态模式默认不启动固定 opencitadel-sandbox 服务，但需镜像供 Worker 创建）
docker compose build opencitadel-sandbox opencitadel-api opencitadel-worker opencitadel-ui

# 构建并启动服务
docker compose up -d --build

# 查看服务状态（含 opencitadel-migrate / opencitadel-api / opencitadel-worker）
docker compose ps
docker compose logs -f
```

> **动态沙箱模式**：`api/config.yaml` 中 `sandbox.address: null` 时，Worker 通过 `docker.sock` 动态创建 `opencitadel-sandbox-*`；compose 中的 `opencitadel-sandbox` 服务已移至 `fixed-sandbox` profile，默认不启动。

> **服务启动顺序**：`opencitadel-postgres` + `opencitadel-redis` → `opencitadel-migrate`（Alembic + LLM Key 迁移）→ `opencitadel-api` + `opencitadel-worker` → `opencitadel-ui` → `opencitadel-nginx`

> **Agent Worker 必须运行**：若 `opencitadel-worker` 未启动，对话请求会写入队列但 Agent 不会执行。可通过 `docker compose logs -f opencitadel-worker` 排查。

### 3.1 Docker 构建期镜像源（可选）

`docker-compose.yml` 已为 Python / npm 服务注入统一 build args，默认使用阿里云 PyPI 与 npmmirror，避免 `files.pythonhosted.org` 下载超时。企业内网可在 `.env` 或 shell 中覆盖：

```bash
# 示例：使用私有 PyPI 代理
export PIP_INDEX_URL=https://pypi.mycompany.internal/simple/
export PIP_TRUSTED_HOST=pypi.mycompany.internal
export UV_INDEX_URL=https://pypi.mycompany.internal/simple/
export UV_HTTP_TIMEOUT=600
export NPM_CONFIG_REGISTRY=https://npm.mycompany.internal/

docker compose build
docker compose up -d
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PIP_INDEX_URL` | 阿里云 PyPI | `pip install uv` |
| `UV_INDEX_URL` | 阿里云 PyPI | `uv sync --frozen` |
| `UV_VERSION` | `0.11.19` | 固定构建期 uv 版本 |
| `UV_HTTP_TIMEOUT` | `300` | `uv sync` 下载 wheel 的 HTTP 超时（秒） |
| `NPM_CONFIG_REGISTRY` | npmmirror | sandbox / ui 的 npm |

Compose 构建后的应用镜像统一命名为：`opencitadel-api`、`opencitadel-worker`、`opencitadel-migrate`、`opencitadel-ui`、`opencitadel-sandbox`。

> **CI/CD 说明**：GitHub Actions 在每次 PR 与 `main` 推送时运行 API 测试（pytest + Postgres/Redis）、UI 测试/构建（Node 22）与 Docker 镜像构建（见 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)）。打 tag（`v*`）后通过 [`.github/workflows/release.yml`](../../.github/workflows/release.yml) 发布多架构镜像到 `ghcr.io/ocealong/opencitadel-*`。生产发布流程：使用 release 镜像或本地 `docker compose build` → 推送到镜像仓库 → `docker compose up` 或 Helm 部署。

---

## ⚙️ 核心配置

### 部署模式 (.env)

`.env` 顶部通过两个变量选择部署模式：

| 模式 | `COMPOSE_PROFILES` | `STORAGE_PROVIDER` | 需填写 |
|------|-------------------|-------------------|--------|
| **cloud**（默认） | 留空 | `cos` | `COS_*` 凭证 |
| **local** | `local` | `minio` | MinIO 默认值开箱可用 |

### cloud 模式配置

```bash
COMPOSE_PROFILES=
STORAGE_PROVIDER=cos

ENV=production
LOG_LEVEL=INFO
API_KEY_SECRET=<openssl rand -hex 32>
JWT_SECRET=<openssl rand -hex 32>
SESSION_SECRET=<openssl rand -hex 32>
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=<STRONG_PASSWORD>
COOKIE_DOMAIN=
COOKIE_SECURE=true
FRONTEND_BASE_URL=https://your-domain.com
OAUTH_REDIRECT_BASE=https://your-domain.com/api/auth/oauth
USE_DB_APP_CONFIG=true

POSTGRES_USER=postgres
POSTGRES_PASSWORD=<STRONG_PASSWORD>
POSTGRES_DB=opencitadel
POSTGRES_HOST=opencitadel-postgres

REDIS_HOST=opencitadel-redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

COS_SECRET_ID=<YOUR_COS_SECRET_ID>
COS_SECRET_KEY=<YOUR_COS_SECRET_KEY>
COS_REGION=ap-guangzhou
COS_BUCKET=<YOUR_BUCKET_NAME>
COS_DOMAIN=<YOUR_COS_DOMAIN>

NGINX_PORT=8088
NGINX_HTTPS_PORT=443
OPENCITADEL_DOMAIN=
HTTPS_ENABLED=false
```

### local 模式配置

```bash
COMPOSE_PROFILES=local
STORAGE_PROVIDER=minio

ENV=production
LOG_LEVEL=INFO
API_KEY_SECRET=<openssl rand -hex 32>
JWT_SECRET=<openssl rand -hex 32>
SESSION_SECRET=<openssl rand -hex 32>
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=<STRONG_PASSWORD>
COOKIE_DOMAIN=
COOKIE_SECURE=true
FRONTEND_BASE_URL=https://your-domain.com
OAUTH_REDIRECT_BASE=https://your-domain.com/api/auth/oauth
USE_DB_APP_CONFIG=true

POSTGRES_USER=postgres
POSTGRES_PASSWORD=<STRONG_PASSWORD>
POSTGRES_DB=opencitadel
POSTGRES_HOST=opencitadel-postgres

REDIS_HOST=opencitadel-redis
REDIS_PORT=6379

# MinIO 默认值开箱可用
MINIO_ENDPOINT=opencitadel-minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=opencitadel
MINIO_SECURE=false

NGINX_PORT=8088
```

本地 LLM：在 UI 添加模型，Provider=ollama，`base_url=http://host.docker.internal:11434/v1`。

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
  image: opencitadel-sandbox
  name_prefix: opencitadel-sandbox
  network: opencitadel-network
  memory_limit: 1g
  pool_enabled: false
  pool_size: 1          # 只预热 1 个；并发任务按需创建，上限见 worker.max_concurrent_tasks
  ttl_minutes: 20
  idle_timeout_minutes: 10
  cleanup_interval_seconds: 60

memory:
  vector_enabled: false
  embedding:
    provider: openai
    model: text-embedding-3-small
    base_url: https://api.openai.com/v1

observability:
  otel_enabled: false
  otel_service_name: opencitadel-api

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
- `llm_models.api_key_encryption` 字段标识存储格式：`legacy_plaintext`（历史明文）或 `fernet_v1`（加密存储）。`opencitadel-migrate` 会在 Alembic 后自动加密历史明文，无需额外命令。
- 系统会自动创建内置 Skill 模板（编程助手、研究分析、数据分析、内容写作），也可在「设置中心 → Skill 模板」维护自定义模板。
- 长期记忆在「设置中心 → 长期记忆」维护，支持全局和会话两种作用域；任务开始时会自动召回相关记忆（时间衰减 + 可选 pgvector 向量混合检索）。
- 开启向量记忆需在 `config.yaml` 设置 `memory.vector_enabled: true`，并在 `.env` 配置 `EMBEDDING_API_KEY`；PostgreSQL 使用 `pgvector/pgvector:pg16` 镜像。
- 会话详情页可查看 Agent 会话内存，并支持压缩、清空或删除单条内存消息。

### 数据库迁移

迁移由 **`opencitadel-migrate` 一次性 init job** 自动执行：先跑 Alembic schema 迁移，再加密历史明文 LLM API Key。API 启动时仅校验 schema 版本，不再在 lifespan 内跑 `alembic upgrade`。

```bash
# 正常部署：docker compose up 会自动运行 opencitadel-migrate
docker compose up -d --build

# 手动执行迁移（版本升级或排查）
docker compose run --rm opencitadel-migrate
# 或进入 api 容器:
docker compose exec opencitadel-api python -m app.migrate

# 本地开发（等价于 python -m app.migrate）
cd api && ./migrate.sh
```

新增迁移版本包括 `memory_entries.embedding vector(1536)`（pgvector 扩展）。

### 存储后端切换与迁移

同一环境从 COS 切换到 MinIO（或反向）时，需先迁移对象数据（数据库只存 key，不记录后端类型）。内置 CLI 支持全桶复制与校验：

```bash
# 1. 确保 .env 同时配置了源端与目标端凭证
# 2. COS -> MinIO（local profile 保证 minio 已启动）
COMPOSE_PROFILES=local docker compose run --rm opencitadel-api \
  python -m app.migrate_storage --source cos --target minio

# 3. 校验
COMPOSE_PROFILES=local docker compose run --rm opencitadel-api \
  python -m app.migrate_storage --source cos --target minio --verify-only

# 4. 切换 .env: STORAGE_PROVIDER=minio，重启
docker compose up -d opencitadel-api opencitadel-worker
```

切换流程：低峰/只读窗口 → 迁移 → 校验 → 改 `STORAGE_PROVIDER` → 重启 → 抽查历史附件/截图/检查点。源端对象保留以便回滚。

可选参数：`--dry-run`（只列差异）、`--prefix logs/`（限定前缀）、`--concurrency 8`（并发数）。

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

### 2. Docker 资源限制

仓库自带的 `docker-compose.yml` 使用顶层 `mem_limit` 与 `cpus`（适用于 `docker compose up`）。示例：

```yaml
services:
  opencitadel-api:
    mem_limit: 640m
    cpus: 2
```

除非使用 Swarm 模式，否则不要依赖 `deploy.resources`。请按宿主机内存预算调整（见下文「内存预算」）。

### 3. 数据备份策略

```bash
# 创建备份脚本
cat > /opt/opencitadel/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/opencitadel"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# 备份 PostgreSQL
docker exec opencitadel-postgres pg_dump -U postgres opencitadel > $BACKUP_DIR/db_$DATE.sql

# 压缩备份
tar -czf $BACKUP_DIR/backup_$DATE.tar.gz -C $BACKUP_DIR db_$DATE.sql
rm $BACKUP_DIR/db_$DATE.sql

# 保留最近7天备份
find $BACKUP_DIR -name "backup_*.tar.gz" -mtime +7 -delete

echo "Backup completed: backup_$DATE.tar.gz"
EOF

chmod +x /opt/opencitadel/backup.sh

# 设置定时任务（每天凌晨2点备份）
crontab -e
# 添加：0 2 * * * /opt/opencitadel/backup.sh >> /var/log/opencitadel-backup.log 2>&1
```

---

## 📊 监控与日志

### 1. 查看服务状态

```bash
# 查看所有容器状态
docker-compose ps

# 查看实时日志
docker-compose logs -f opencitadel-api
docker-compose logs -f opencitadel-ui
docker-compose logs -f opencitadel-nginx

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
docker exec opencitadel-postgres pg_isready -U postgres

# Worker 运行状态
docker compose logs --tail=50 opencitadel-worker
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
cd /opt/opencitadel
docker-compose up -d

# 停止所有服务
docker-compose down

# 重启单个服务
docker compose restart opencitadel-api
docker compose restart opencitadel-worker

# 扩展 Worker 副本（需移除 compose 中 container_name 或使用 scale profile）
# docker compose up -d --scale opencitadel-worker=2

# 重新构建并启动
docker-compose up -d --build

# 查看服务日志
docker-compose logs -f --tail=100 opencitadel-api
```

### 版本更新

```bash
cd /opt/opencitadel
git pull origin main
docker compose build
docker compose up -d --build
docker image prune -f
```

### 数据库维护

```bash
# 进入数据库
docker exec -it opencitadel-postgres psql -U postgres -d opencitadel

# 执行迁移
docker compose run --rm opencitadel-migrate

# 备份恢复
docker exec -i opencitadel-postgres psql -U postgres opencitadel < backup.sql
```

### LLM API Key 迁移

常规部署/升级只需 `docker compose up -d --build`，`opencitadel-migrate` 会自动完成历史明文加密。迁移日志只输出统计信息与 `model_id`，不会打印真实 API Key。

若升级后迁移容器未重建，可补救：

```bash
docker compose up -d --build --force-recreate opencitadel-migrate opencitadel-api opencitadel-worker
```

极端情况下可单独修复：

```bash
docker compose run --rm opencitadel-api python -m app.migrate_llm_api_keys
```

轮换 `API_KEY_SECRET` 后，已加密的模型密钥需在前端重新保存。

---

## 🛠️ 故障排查

### 常见问题

#### 1. Docker 构建失败（`uv sync` 超时）

若 `docker compose build` 在 `RUN uv sync --frozen` 阶段失败，日志出现 `Failed to download` 或 `UV_HTTP_TIMEOUT current value: 30s`：

```bash
# 确认 build args 已传入（应看到 UV_HTTP_TIMEOUT: "300"）
docker compose config | grep -A5 UV_HTTP_TIMEOUT

# 弱网环境可提高超时（秒）
export UV_HTTP_TIMEOUT=600
docker compose build opencitadel-api opencitadel-worker opencitadel-migrate opencitadel-sandbox

# 同时确认 PyPI 镜像源
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
docker compose build opencitadel-api
```

构建成功后，应用镜像名应为 `opencitadel-api`、`opencitadel-worker`、`opencitadel-migrate`、`opencitadel-ui`、`opencitadel-sandbox`，而非 `opencitadel-opencitadel-*`。

#### 2. 容器启动失败

```bash
# 查看详细日志
docker compose logs opencitadel-api

# 检查配置文件
docker exec -it opencitadel-api printenv API_KEY_SECRET ENV SQLALCHEMY_DATABASE_URI
docker exec -it opencitadel-api cat /app/config.yaml

# 验证网络连接
docker network inspect opencitadel-network
```

#### 3. 数据库连接失败

若 `opencitadel-migrate` 报 `password authentication failed for user "postgres"`：

- `opencitadel-postgres` 与 `opencitadel-migrate` 现在都从 `POSTGRES_*` 派生连接串；不要只改 `POSTGRES_PASSWORD` 却保留旧的 `SQLALCHEMY_DATABASE_URI`。
- PostgreSQL 数据卷只在**首次初始化**时写入密码；之后仅改 `.env` 不会自动更新卷内密码。
- 让数据库密码与 `.env` 对齐：`ALTER USER postgres WITH PASSWORD '<POSTGRES_PASSWORD>';`
- 全新环境可 `docker compose down -v` 后重建（会删除数据库数据）。

```bash
# 检查数据库状态
docker compose logs opencitadel-postgres

# 测试连接
docker exec opencitadel-postgres pg_isready -U postgres -d opencitadel

# 查看 migrate 实际使用的连接参数（URI 由 POSTGRES_* 派生）
docker compose run --rm opencitadel-migrate python -c "from core.config import get_settings; print(get_settings().sqlalchemy_database_uri)"

# 重置密码（与 .env 中 POSTGRES_PASSWORD 保持一致）
docker exec -it opencitadel-postgres psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'new_password';"
```

#### 4. 内存不足 / Swap 抖动

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
docker ps -a --filter "name=opencitadel-sandbox-"

# 宿主机调优（4G swap 兜底 + vm.swappiness=10 + Docker 日志轮转）
sudo bash deploy/scripts/host-tune.sh

# 应用右配后的 compose 与 config（见 docker-compose.yml / api/config.yaml）
cd /opt/opencitadel && docker compose up -d --build

# 清理未使用的镜像/容器（勿随意 --volumes，会删数据库卷）
docker system prune -a -f
```

**内存预算（16GB 主机，已右配）**

| 服务 | mem_limit |
|------|-----------|
| postgres | 1024m |
| api | 640m |
| worker | 1024m |
| ui | 384m |
| redis | 512m |
| nginx | 128m |
| 沙箱（1 预热 + 最多 3 按需） | 1~4g |

#### 5. Nginx 502 错误

```bash
# 检查后端服务
docker-compose ps opencitadel-api opencitadel-ui

# 检查 Nginx 配置
docker exec opencitadel-nginx nginx -t

# 重载 Nginx
docker exec opencitadel-nginx nginx -s reload
```

---

## 🔄 内存安全架构升级与回滚

### 升级（已有实例）

```bash
# 1. 备份
docker exec opencitadel-postgres pg_dump -U postgres opencitadel > backup_$(date +%Y%m%d).sql
cp .env .env.bak && cp api/config.yaml api/config.yaml.bak

# 2. 拉取代码并重建
git pull
docker compose build opencitadel-sandbox opencitadel-api opencitadel-worker opencitadel-ui
docker compose up -d

# 3. 验证 Worker 启动 reconcile（收编存量 opencitadel-sandbox-*）
docker compose logs opencitadel-worker | tail -50
docker stats
free -m
```

### 回滚

无数据库 schema 变更，恢复旧配置即可：

```bash
cp .env.bak .env && cp api/config.yaml.bak api/config.yaml
docker compose up -d
```

### 新增配置项（api/config.yaml worker/sandbox 段）

| 配置 | 默认 | 说明 |
|------|------|------|
| `sandbox.driver` | `auto` | `docker` / `kubernetes` |
| `worker.max_sandboxes_per_node` | 4 | 节点沙箱配额硬上限 |
| `worker.admission_min_host_available_mb` | 3072 | 低于此值不新建沙箱 |
| `worker.admission_reclaim_enabled` | true | 低内存主动回收空闲沙箱 |
| `sandbox.pool_enabled` | false | 关闭常驻预热沙箱 |

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

已在 [docker-compose.yml](../../docker-compose.yml) 与 [api/config.yaml](../../api/config.yaml) 右配：

- 核心服务 mem_limit 合计约 **3.7GB**（postgres 1G / worker 1G / api 640M / ui 384M / redis 512M / nginx 128M）
- 沙箱：**按需创建**（`pool_enabled: false`），`memory_limit: 1g`
- 沙箱并发由 **Redis 节点配额** `max_sandboxes_per_node` + **内存水位** `admission_min_host_available_mb` 双重控制
- 任务并发仍由 `worker.max_concurrent_tasks` 控制（与沙箱配额独立）

### 3. PostgreSQL 调优

Postgres 参数已内置于 `docker-compose.yml` 的 `command`（匹配 1GB 容器配额）：

- `shared_buffers = 256MB`
- `effective_cache_size = 768MB`
- `work_mem = 8MB`
- `maintenance_work_mem = 64MB`

修改后执行：`docker compose up -d opencitadel-postgres`

### 4. Redis 优化

已在 docker-compose.yml 中配置：
- 最大内存：256MB
- 淘汰策略：allkeys-lru
- AOF 持久化：开启

### 5. 架构演进

单机稳定后若需水平扩展，见 [架构演进指南](../architecture/architecture-evolution.zh-CN.md)（DB/Redis 外置、K8s HPA、沙箱外置）。

---

## 🔐 HTTPS 配置（可选）

默认 HTTP 即可使用（`http://服务器IP:8088`）。启用 HTTPS 只需在 `.env` 中设置域名与证书相关变量并重启 Nginx，无需手动改 Nginx 或 Compose 文件。

```bash
# .env
OPENCITADEL_DOMAIN=your-domain.com
HTTPS_ENABLED=true
NGINX_PORT=8088
NGINX_HTTPS_PORT=443

docker compose up -d opencitadel-nginx
```

域名绑定、证书准备（Let's Encrypt 或自有证书）、验证与回滚，详见 **[HTTPS 配置](https-domain-setup.zh-CN.md)**。

---

## ☸️ Kubernetes / Helm 部署

Helm Chart 位于 `deploy/helm/opencitadel/`，支持全栈部署（Postgres/Redis/UI/Ingress + API/Worker + K8s Pod 沙箱 driver）。

```bash
# 构建并推送四镜像
docker build --target api -t your-registry/opencitadel-api ./api
docker build --target worker -t your-registry/opencitadel-worker ./api
docker build -t your-registry/opencitadel-ui ./ui
docker build -t your-registry/opencitadel-sandbox ./sandbox
docker push your-registry/opencitadel-api your-registry/opencitadel-worker your-registry/opencitadel-ui your-registry/opencitadel-sandbox

helm upgrade --install opencitadel ./deploy/helm/opencitadel \
  --set image.api.repository=your-registry/opencitadel-api \
  --set image.worker.repository=your-registry/opencitadel-worker \
  --set image.ui.repository=your-registry/opencitadel-ui \
  --set image.sandbox.repository=your-registry/opencitadel-sandbox \
  --set appConfig.sandbox.driver=kubernetes \
  --set ingress.enabled=true \
  --set replicaCount.worker=2
```

Chart 特性：
- 进集群 **PostgreSQL(pgvector) / Redis**（StatefulSet + PVC）
- **UI + Ingress**（`/` → UI，`/api` → API）
- Worker **ServiceAccount + RBAC**（pods create/delete/get/list）供 K8s 沙箱 driver
- kubernetes driver 下 **不挂载 docker.sock**
- 准入/回收逻辑与单机 compose **同一套 Redis 节点配额**

---

## 🆘 技术支持

- **项目文档**: [README.zh-CN.md](../../README.zh-CN.md) · [文档中心](../README.zh-CN.md)
- **健康检查**: `GET http://YOUR_SERVER_IP:8088/api/status`（经 Nginx）
- **OpenAPI（内网调试）**: FastAPI 的 `/docs` 仅在 API 容器 8000 端口提供，Nginx 未在 `:8088` 暴露。可用 `docker compose exec opencitadel-api curl -s localhost:8000/docs` 或 port-forward 调试。
- **日志位置**: `docker compose logs`
- **数据目录**: `/var/lib/docker/volumes`

---

**最后更新时间**: 2026-06-11
**适用版本**: OpenCitadel v1.0  
**部署环境**: Ubuntu 24.04 LTS, 8核/16GB/270GB SSD/18Mbps
