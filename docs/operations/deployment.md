[简体中文](deployment.zh-CN.md)

# OpenCitadel Production Deployment Guide

## 📋 Server recommendations

| Item | Recommendation |
|------|----------------|
| **OS** | Ubuntu 24.04 LTS or equivalent Linux |
| **CPU / RAM** | Production: 8 cores / 16 GB minimum |
| **Disk** | 100 GB+ SSD; scale with file and log retention |
| **Bandwidth** | Size for user count and file upload volume |

---

## 🚀 Quick deploy (5 minutes)

For a local trial with minimal setup, see [Self-host in 10 minutes](../tutorials/01-self-host-10-minutes.md).

### 1. Server initialization

```bash
# SSH into the server
ssh root@YOUR_SERVER_IP

# Update the system
sudo apt update && sudo apt upgrade -y

# Install basic tools
sudo apt install -y curl wget git vim ufw
```

### 2. Install Docker

```bash
# Install Docker
curl -fsSL https://get.docker.com | bash

# Enable and start Docker
sudo systemctl enable docker
sudo systemctl start docker

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Verify installation
docker --version
docker-compose --version

# Add the current user to the docker group (avoid sudo on every command)
sudo usermod -aG docker $USER
newgrp docker
```

### 3. Deploy the application

```bash
# Clone the repository
cd /opt
git clone https://github.com/OceaLong/opencitadel.git opencitadel
cd opencitadel

# Create environment file
cp .env.example .env

# Edit configuration (see configuration section below)
vim .env
vim api/config.yaml

# Build sandbox image (dynamic mode does not start a fixed opencitadel-sandbox service by default, but the image is required for Worker-created sandboxes)
docker compose build opencitadel-sandbox opencitadel-api opencitadel-worker opencitadel-ui

# Build and start services
docker compose up -d --build

# Check service status (includes opencitadel-migrate / opencitadel-api / opencitadel-worker)
docker compose ps
docker compose logs -f
```

> **Dynamic sandbox mode**: When `sandbox.address: null` in `api/config.yaml`, the Worker creates `opencitadel-sandbox-*` containers via `docker.sock`. The compose `opencitadel-sandbox` service is under the `fixed-sandbox` profile and is not started by default.

> **Startup order**: `opencitadel-postgres` + `opencitadel-redis` → `opencitadel-migrate` (Alembic + LLM key migration) → `opencitadel-api` + `opencitadel-worker` → `opencitadel-ui` → `opencitadel-nginx`

> **Agent Worker is required**: If `opencitadel-worker` is not running, chat requests are queued but agents will not execute. Use `docker compose logs -f opencitadel-worker` to troubleshoot.

### 3.1 Docker build mirror settings (optional)

`docker-compose.yml` injects unified build args for Python and npm services. Defaults use Aliyun PyPI and npmmirror to avoid `files.pythonhosted.org` timeouts. Override in `.env` or your shell for corporate networks:

```bash
# Example: private PyPI proxy
export PIP_INDEX_URL=https://pypi.mycompany.internal/simple/
export PIP_TRUSTED_HOST=pypi.mycompany.internal
export UV_INDEX_URL=https://pypi.mycompany.internal/simple/
export UV_HTTP_TIMEOUT=600
export NPM_CONFIG_REGISTRY=https://npm.mycompany.internal/

docker compose build
docker compose up -d
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIP_INDEX_URL` | Aliyun PyPI | `pip install uv` |
| `UV_INDEX_URL` | Aliyun PyPI | `uv sync --frozen` |
| `UV_VERSION` | `0.11.19` | Pinned uv version at build time |
| `UV_HTTP_TIMEOUT` | `300` | HTTP timeout (seconds) for `uv sync` wheel downloads |
| `NPM_CONFIG_REGISTRY` | npmmirror | npm for sandbox / ui |

Built application images are named: `opencitadel-api`, `opencitadel-worker`, `opencitadel-migrate`, `opencitadel-ui`, `opencitadel-sandbox`.

> **CI/CD note**: GitHub Actions runs API tests (pytest + Postgres/Redis), UI tests/build (Node 22), and Docker image builds on every PR and push to `main` (see [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)). Tagged releases (`v*`) publish multi-arch images to `ghcr.io/ocealong/opencitadel-*` via [`.github/workflows/release.yml`](../../.github/workflows/release.yml). Production release flow: use release images or `docker compose build` locally → push to your registry → `docker compose up` or Helm deploy.

---

## ⚙️ Core configuration

### Deployment mode (.env)

Choose the deployment mode with two variables at the top of `.env`:

```mermaid
flowchart TD
  Start["Choose deployment mode"] --> Profile{"COMPOSE_PROFILES"}
  Profile -->|"empty"| Cloud["cloud mode"]
  Profile -->|"local"| Local["local mode"]
  Cloud --> Cos["STORAGE_PROVIDER=cos"]
  Local --> Minio["STORAGE_PROVIDER=minio"]
  Cos --> CosCreds["Set COS_* credentials"]
  Minio --> MinioUp["MinIO via local profile"]
  Start --> SandboxDriver{"sandbox.driver"}
  SandboxDriver -->|"auto/docker"| DockerSock["Worker mounts docker.sock"]
  SandboxDriver -->|"kubernetes"| K8sRBAC["Worker SA creates Pods"]
  DockerSock --> BuildImg["Build opencitadel-sandbox image"]
```

| Mode | `COMPOSE_PROFILES` | `STORAGE_PROVIDER` | Required |
|------|-------------------|-------------------|----------|
| **cloud** (default) | empty | `cos` | `COS_*` credentials |
| **local** | `local` | `minio` | MinIO defaults work out of the box |

### cloud mode

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

### local mode

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

# MinIO defaults work out of the box
MINIO_ENDPOINT=opencitadel-minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=opencitadel
MINIO_SECURE=false

NGINX_PORT=8088
```

For local LLM: add a model in the UI with Provider=ollama, `base_url=http://host.docker.internal:11434/v1`.

Behavior settings (CORS, rate limits, sandbox, memory, worker concurrency, OTEL, etc.) belong in `api/config.yaml`, not `.env`.

### Runtime configuration (api/config.yaml)

Docker Compose mounts `./api/config.yaml` into API/Worker containers at `/app/config.yaml`.

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
  pool_size: 1          # Pre-warm only 1; concurrent tasks create on demand, capped by worker.max_concurrent_tasks
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

See [MCP integrations](../tutorials/03-mcp-integrations.md) for configuring MCP servers.

### Models, Skills, and memory

- **Default models are not imported on first boot.** In Settings → Model Management, add an **endpoint** (Provider / Base URL / API Key) first, then add multiple **models** under the same endpoint (differing only by model name), and set a default before starting a chat. Connection settings live in PostgreSQL `llm_endpoints`; models live in `llm_models`. API keys are encrypted with `API_KEY_SECRET`.
- The `llm_endpoints.api_key_encryption` field indicates storage format: `legacy_plaintext` (historical plaintext) or `fernet_v1` (encrypted). `opencitadel-migrate` encrypts legacy plaintext automatically after Alembic—no extra command needed. Updating an endpoint URL or API key applies to all models under that endpoint.
- Built-in Skill templates (coding assistant, research, data analysis, content writing) are created automatically; customize them in Settings → Skill Templates.
- Long-term memory is managed in Settings → Long-term Memory (global and session scopes). Relevant memories are recalled at task start (time decay + optional pgvector hybrid search).
- Enable vector memory with `memory.vector_enabled: true` in `config.yaml` and `EMBEDDING_API_KEY` in `.env`. PostgreSQL uses the `pgvector/pgvector:pg16` image.
- Session detail pages show agent session memory with compress, clear, or delete actions per message.

### Database migrations

Migrations run automatically via the **`opencitadel-migrate` one-shot init job**: Alembic schema migrations first, then encryption of legacy plaintext LLM API keys. The API only validates schema version at startup—it no longer runs `alembic upgrade` in lifespan.

```bash
# Normal deploy: docker compose up runs opencitadel-migrate
docker compose up -d --build

# Manual migration (version upgrade or troubleshooting)
docker compose run --rm opencitadel-migrate
# Or inside the api container:
docker compose exec opencitadel-api python -m app.migrate

# Local development (equivalent to python -m app.migrate)
cd api && ./migrate.sh
```

Recent migrations include `memory_entries.embedding vector(1536)` (pgvector extension).

### Storage backend switch and migration

When switching COS ↔ MinIO in the same environment, migrate object data first (the database stores keys only, not backend type). A built-in CLI supports full-bucket copy and verification:

```bash
# 1. Ensure .env has credentials for both source and target
# 2. COS -> MinIO (local profile ensures minio is running)
COMPOSE_PROFILES=local docker compose run --rm opencitadel-api \
  python -m app.migrate_storage --source cos --target minio

# 3. Verify
COMPOSE_PROFILES=local docker compose run --rm opencitadel-api \
  python -m app.migrate_storage --source cos --target minio --verify-only

# 4. Switch .env: STORAGE_PROVIDER=minio, then restart
docker compose up -d opencitadel-api opencitadel-worker
```

Recommended flow: low-traffic / read-only window → migrate → verify → change `STORAGE_PROVIDER` → restart → spot-check historical attachments, screenshots, checkpoints. Keep source objects for rollback.

Optional flags: `--dry-run` (list differences only), `--prefix logs/` (limit prefix), `--concurrency 8`.

---

## 🔒 Security hardening

### 1. Firewall

```bash
# Enable UFW
sudo ufw enable

# Allow SSH
sudo ufw allow 22/tcp

# Allow application port
sudo ufw allow 8088/tcp

# View rules
sudo ufw status verbose
```

See [Security model](../architecture/security-model.md) for trust boundaries and sandbox isolation.

### 2. Docker resource limits

OpenCitadel's Compose file uses top-level `mem_limit` and `cpus` on each service (compatible with `docker compose up`). Example from the shipped `docker-compose.yml`:

```yaml
services:
  opencitadel-api:
    mem_limit: 640m
    cpus: 2
```

Do **not** rely on `deploy.resources` unless you run Compose in Swarm mode. Adjust limits to match your host memory budget (see [Memory budget](#memory-budget-16-gb-host-right-sized) below).

### 3. Backup strategy

```bash
# Create backup script
cat > /opt/opencitadel/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/opencitadel"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup PostgreSQL
docker exec opencitadel-postgres pg_dump -U postgres opencitadel > $BACKUP_DIR/db_$DATE.sql

# Compress backup
tar -czf $BACKUP_DIR/backup_$DATE.tar.gz -C $BACKUP_DIR db_$DATE.sql
rm $BACKUP_DIR/db_$DATE.sql

# Keep last 7 days
find $BACKUP_DIR -name "backup_*.tar.gz" -mtime +7 -delete

echo "Backup completed: backup_$DATE.tar.gz"
EOF

chmod +x /opt/opencitadel/backup.sh

# Cron (daily at 2 AM)
crontab -e
# Add: 0 2 * * * /opt/opencitadel/backup.sh >> /var/log/opencitadel-backup.log 2>&1
```

---

## 📊 Monitoring and logs

### 1. Service status

```bash
# All container status
docker-compose ps

# Live logs
docker-compose logs -f opencitadel-api
docker-compose logs -f opencitadel-ui
docker-compose logs -f opencitadel-nginx

# Resource usage
docker stats
```

### 2. Health checks

```bash
# API health
curl http://localhost:8088/api/status

# Prometheus metrics
curl http://localhost:8088/api/metrics

# Frontend
curl -I http://localhost:8088

# Database
docker exec opencitadel-postgres pg_isready -U postgres

# Worker
docker compose logs --tail=50 opencitadel-worker
```

### 3. Log rotation

```bash
# Configure Docker log rotation
cat > /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF

# Restart Docker
sudo systemctl restart docker
```

---

## 🔄 Operations

### Service management

```bash
# Start all services
cd /opt/opencitadel
docker-compose up -d

# Stop all services
docker-compose down

# Restart a single service
docker compose restart opencitadel-api
docker compose restart opencitadel-worker

# Scale Worker replicas (remove container_name in compose or use a scale profile)
# docker compose up -d --scale opencitadel-worker=2

# Rebuild and start
docker-compose up -d --build

# Service logs
docker-compose logs -f --tail=100 opencitadel-api
```

### Version updates

```bash
cd /opt/opencitadel
git pull origin main
docker compose build
docker compose up -d --build
docker image prune -f
```

### Database maintenance

```bash
# Open psql
docker exec -it opencitadel-postgres psql -U postgres -d opencitadel

# Run migrations
docker compose run --rm opencitadel-migrate

# Restore from backup
docker exec -i opencitadel-postgres psql -U postgres opencitadel < backup.sql
```

### LLM API key migration

Normal deploy/upgrade only requires `docker compose up -d --build`; `opencitadel-migrate` encrypts legacy plaintext automatically. Migration logs print statistics and `model_id` only—never real API keys.

If the migrate container was not recreated after upgrade:

```bash
docker compose up -d --build --force-recreate opencitadel-migrate opencitadel-api opencitadel-worker
```

Manual repair if needed:

```bash
docker compose run --rm opencitadel-api python -m app.migrate_llm_api_keys
```

After rotating `API_KEY_SECRET`, re-save encrypted endpoint keys in Settings → Model Management → Edit endpoint.

---

## 🛠️ Troubleshooting

### Common issues

#### 1. Docker build failure (`uv sync` timeout)

If `docker compose build` fails at `RUN uv sync --frozen` with `Failed to download` or `UV_HTTP_TIMEOUT current value: 30s`:

```bash
# Confirm build args (should show UV_HTTP_TIMEOUT: "300")
docker compose config | grep -A5 UV_HTTP_TIMEOUT

# Increase timeout on slow networks (seconds)
export UV_HTTP_TIMEOUT=600
docker compose build opencitadel-api opencitadel-worker opencitadel-migrate opencitadel-sandbox

# Confirm PyPI mirror
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
docker compose build opencitadel-api
```

Built images should be named `opencitadel-api`, `opencitadel-worker`, `opencitadel-migrate`, `opencitadel-ui`, `opencitadel-sandbox`—not `opencitadel-opencitadel-*`.

#### 2. Container startup failure

```bash
# Detailed logs
docker compose logs opencitadel-api

# Check configuration
docker exec -it opencitadel-api printenv API_KEY_SECRET ENV SQLALCHEMY_DATABASE_URI
docker exec -it opencitadel-api cat /app/config.yaml

# Network
docker network inspect opencitadel-network
```

#### 3. Database connection failure

If `opencitadel-migrate` reports `password authentication failed for user "postgres"`:

- Both `opencitadel-postgres` and `opencitadel-migrate` derive the connection string from `POSTGRES_*`; do not change only `POSTGRES_PASSWORD` while keeping an old `SQLALCHEMY_DATABASE_URI`.
- PostgreSQL volume password is set only on **first initialization**; changing `.env` later does not update the volume password.
- Align DB password with `.env`: `ALTER USER postgres WITH PASSWORD '<POSTGRES_PASSWORD>';`
- Fresh environment: `docker compose down -v` then rebuild (destroys database data).

```bash
# Database status
docker compose logs opencitadel-postgres

# Connection test
docker exec opencitadel-postgres pg_isready -U postgres -d opencitadel

# URI derived from POSTGRES_* (migrate container)
docker compose run --rm opencitadel-migrate python -c "from core.config import get_settings; print(get_settings().sqlalchemy_database_uri)"

# Reset password (match POSTGRES_PASSWORD in .env)
docker exec -it opencitadel-postgres psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'new_password';"
```

#### 4. Memory pressure / swap thrashing

On a 16 GB host, sustained memory >95% with high disk read IO usually means **overcommit + swap paging**, not CPU shortage.

```bash
# Collect before/after metrics (non-zero si/so = swap thrashing)
bash deploy/scripts/verify-host-health.sh before
bash deploy/scripts/verify-host-health.sh after

# Memory and container quotas
free -h
swapon --show
vmstat 1 5
docker stats --no-stream
docker ps -a --filter "name=opencitadel-sandbox-"

# Host tuning (4G swap safety net + vm.swappiness=10 + Docker log rotation)
sudo bash deploy/scripts/host-tune.sh

# Apply right-sized compose and config (see docker-compose.yml / api/config.yaml)
cd /opt/opencitadel && docker compose up -d --build

# Prune unused images/containers (avoid --volumes; deletes DB volumes)
docker system prune -a -f
```

**Memory budget (16 GB host, right-sized)**

| Service | mem_limit |
|---------|-----------|
| postgres | 1024m |
| api | 640m |
| worker | 1024m |
| ui | 384m |
| redis | 512m |
| nginx | 128m |
| sandboxes (1 pre-warm + up to 3 on demand) | 1~4g |

#### 5. Nginx 502

```bash
# Backend services
docker-compose ps opencitadel-api opencitadel-ui

# Nginx config test
docker exec opencitadel-nginx nginx -t

# Reload Nginx
docker exec opencitadel-nginx nginx -s reload
```

---

## 🔄 Memory-safe architecture upgrade and rollback

### Upgrade (existing instance)

```bash
# 1. Backup
docker exec opencitadel-postgres pg_dump -U postgres opencitadel > backup_$(date +%Y%m%d).sql
cp .env .env.bak && cp api/config.yaml api/config.yaml.bak

# 2. Pull and rebuild
git pull
docker compose build opencitadel-sandbox opencitadel-api opencitadel-worker opencitadel-ui
docker compose up -d

# 3. Verify Worker startup reconcile (adopts existing opencitadel-sandbox-*)
docker compose logs opencitadel-worker | tail -50
docker stats
free -m
```

### Rollback

No database schema change—restore old config:

```bash
cp .env.bak .env && cp api/config.yaml.bak api/config.yaml
docker compose up -d
```

### New settings (api/config.yaml worker/sandbox)

| Setting | Default | Description |
|---------|---------|-------------|
| `sandbox.driver` | `auto` | `docker` / `kubernetes` |
| `worker.max_sandboxes_per_node` | 4 | Hard per-node sandbox quota |
| `worker.admission_min_host_available_mb` | 3072 | Do not create sandboxes below this free memory |
| `worker.admission_reclaim_enabled` | true | Reclaim idle sandboxes under memory pressure |
| `sandbox.pool_enabled` | false | Disable always-on pre-warmed sandboxes |

---

## 📈 Performance tuning

### 1. Host tuning (recommended after first deploy)

```bash
# One-shot: vm.swappiness=10, 4G swap safety net, Docker log rotation
sudo bash deploy/scripts/host-tune.sh

# Verify (si/so should be 0 after tuning; memory idle <80%)
bash deploy/scripts/verify-host-health.sh after
```

> **Do not** run `swapoff -a` while memory is still overcommitted: swap thrashing becomes OOM kills. Right-size `docker-compose.yml` and `api/config.yaml` first, then keep a small swap as a safety net.

### 2. Container and sandbox quotas

Right-sized in [docker-compose.yml](../../docker-compose.yml) and [api/config.yaml](../../api/config.yaml):

- Core services mem_limit total ~**3.7 GB** (postgres 1G / worker 1G / api 640M / ui 384M / redis 512M / nginx 128M)
- Sandboxes: **on-demand** (`pool_enabled: false`), `memory_limit: 1g`
- Sandbox concurrency: Redis node quota `max_sandboxes_per_node` + memory watermark `admission_min_host_available_mb`
- Task concurrency: `worker.max_concurrent_tasks` (independent of sandbox quota)

### 3. PostgreSQL tuning

Postgres parameters are set in `docker-compose.yml` `command` (matched to 1 GB container limit):

- `shared_buffers = 256MB`
- `effective_cache_size = 768MB`
- `work_mem = 8MB`
- `maintenance_work_mem = 64MB`

After changes: `docker compose up -d opencitadel-postgres`

### 4. Redis

Configured in docker-compose.yml:
- Max memory: 256 MB
- Eviction: allkeys-lru
- AOF persistence: enabled

### 5. Architecture evolution

For horizontal scale after a stable single node, see [Architecture evolution](../architecture/architecture-evolution.md) (external DB/Redis, K8s HPA, external sandbox).

---

## 🔐 HTTPS (optional)

HTTP works out of the box at `http://SERVER_IP:8088`. To enable HTTPS, set domain and certificate variables in `.env` and restart Nginx—no manual Nginx or Compose file edits.

```bash
# .env
OPENCITADEL_DOMAIN=your-domain.com
HTTPS_ENABLED=true
NGINX_PORT=8088
NGINX_HTTPS_PORT=443

docker compose up -d opencitadel-nginx
```

Full domain binding, certificate setup (Let's Encrypt or custom), verification, and rollback: **[HTTPS & domain setup](https-domain-setup.md)**.

---

## ☸️ Kubernetes / Helm deployment

Helm chart at `deploy/helm/opencitadel/` supports full-stack deploy (Postgres/Redis/UI/Ingress + API/Worker + K8s Pod sandbox driver).

```bash
# Build and push five images (api, worker, migrate reuses api image tag)
docker build --target api -t your-registry/opencitadel-api ./api
docker build --target worker -t your-registry/opencitadel-worker ./api
docker build --target api -t your-registry/opencitadel-migrate ./api
docker build -t your-registry/opencitadel-ui ./ui
docker build -t your-registry/opencitadel-sandbox ./sandbox
docker push your-registry/opencitadel-api your-registry/opencitadel-worker your-registry/opencitadel-migrate your-registry/opencitadel-ui your-registry/opencitadel-sandbox

> **Helm note**: the migrate initContainer reuses `image.api` (same Dockerfile target). The separate `opencitadel-migrate` tag is used by Docker Compose one-off jobs and release publishing.

helm upgrade --install opencitadel ./deploy/helm/opencitadel \
  --set image.api.repository=your-registry/opencitadel-api \
  --set image.worker.repository=your-registry/opencitadel-worker \
  --set image.ui.repository=your-registry/opencitadel-ui \
  --set image.sandbox.repository=your-registry/opencitadel-sandbox \
  --set appConfig.sandbox.driver=kubernetes \
  --set ingress.enabled=true \
  --set replicaCount.worker=2
```

Chart features:
- In-cluster **PostgreSQL (pgvector) / Redis** (StatefulSet + PVC)
- **UI + Ingress** (`/` → UI, `/api` → API)
- Worker **ServiceAccount + RBAC** (pods create/delete/get/list) for K8s sandbox driver
- **No docker.sock mount** under kubernetes driver
- Same admission/reclaim logic as single-node compose via **Redis node quota**

Details: [Helm chart README](../../deploy/helm/opencitadel/README.md).

---

## 🆘 Support

- **Project docs**: [README.md](../../README.md) · [Documentation index](../README.md)
- **Health check**: `GET http://YOUR_SERVER_IP:8088/api/status` (via Nginx)
- **OpenAPI (internal)**: FastAPI serves `/docs` on the API container port 8000 only; Nginx does not expose it on `:8088`. Use `docker compose exec opencitadel-api curl -s localhost:8000/docs` or port-forward for debugging.
- **Logs**: `docker compose logs`
- **Data volumes**: `/var/lib/docker/volumes`

---

**Last updated**: 2026-06-11  
**Applies to**: OpenCitadel v1.0  
**Reference environment**: Ubuntu 24.04 LTS, 8 cores / 16 GB / 270 GB SSD / 18 Mbps
