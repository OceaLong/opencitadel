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
cp api/.env.example .env

# 编辑配置文件（见下方配置说明）
vim .env
vim api/config.yaml

# 构建并启动服务
docker-compose up -d --build

# 查看服务状态
docker-compose ps
docker-compose logs -f
```

---

## ⚙️ 核心配置

### 环境变量配置 (.env)

```bash
# ==================== 基础配置 ====================
ENV=production
LOG_LEVEL=INFO
APP_CONFIG_FILEPATH=config.yaml

# ==================== 数据库配置 ====================
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<STRONG_PASSWORD>
POSTGRES_DB=manus
SQLALCHEMY_DATABASE_URI=postgresql+asyncpg://postgres:<STRONG_PASSWORD>@manus-postgres:5432/manus

# ==================== Redis 配置 ====================
REDIS_HOST=manus-redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# ==================== 腾讯云 COS 配置 ====================
COS_SECRET_ID=<YOUR_COS_SECRET_ID>
COS_SECRET_KEY=<YOUR_COS_SECRET_KEY>
COS_REGION=ap-guangzhou
COS_SCHEME=https
COS_BUCKET=<YOUR_BUCKET_NAME>
COS_DOMAIN=<YOUR_COS_DOMAIN>

# ==================== 沙箱配置 ====================
SANDBOX_ADDRESS=http://manus-sandbox:8080
SANDBOX_IMAGE=manus-sandbox
SANDBOX_NAME_PREFIX=my-manus-sandbox
SANDBOX_TTL_MINUTES=60
SANDBOX_NETWORK=manus-network
SANDBOX_CHROME_ARGS=--no-sandbox --disable-dev-shm-usage
SANDBOX_HTTPS_PROXY=
SANDBOX_HTTP_PROXY=
SANDBOX_NO_PROXY=localhost,127.0.0.1

# ==================== Nginx 端口 ====================
NGINX_PORT=8088
```

### LLM 配置 (api/config.yaml)

`llm_config` 用于首次启动时种子化默认模型（可选）。仅当 `base_url`、`model_name` 齐全且（非 Ollama 时）提供了 `api_key` 时才会导入；未配置时系统保持无模型，发起对话会提示先在设置中添加模型。服务启动后，请在前端「设置中心 → 模型管理」维护模型配置、设置默认模型，并在新建会话或会话详情页选择会话级模型。

```yaml
llm_config:
  base_url: https://api.deepseek.com/
  api_key: sk-YOUR_API_KEY_HERE
  model_name: deepseek-chat
  temperature: 0.7
  max_tokens: 8192

agent_config:
  max_iterations: 100
  max_retries: 3
  max_search_results: 10

mcp_config:
  mcpServers:
    amap-maps-streamableHTTP:
      transport: streamable_http
      enabled: true
      url: https://mcp.amap.com/mcp?key=YOUR_AMAP_KEY
    jina-mcp-server:
      transport: streamable_http
      enabled: true
      url: https://mcp.jina.ai/v1
      headers:
        Authorization: Bearer YOUR_JINA_TOKEN

a2a_config:
  a2a_servers: []
```

### 模型、Skill 与记忆

- 首次启动且 `llm_config` 配置完整时会自动导入一个默认模型；未配置则保持无模型。后续模型新增、编辑、删除和默认模型切换都在「设置中心 → 模型管理」完成。若数据库已有模型，修改 `api/config.yaml` 不会改变当前运行时默认模型。
- 系统会自动创建内置 Skill 模板（编程助手、研究分析、数据分析、内容写作），也可在「设置中心 → Skill 模板」维护自定义模板。
- 长期记忆在「设置中心 → 长期记忆」维护，支持全局和会话两种作用域；任务开始时会自动召回相关记忆。
- 会话详情页可查看 Agent 会话内存，并支持压缩、清空或删除单条内存消息。

### 数据库迁移

本版本新增 `llm_models`、`skills`、`memory_entries` 表，并为 `sessions` 表新增 `model_id`、`skill_id` 字段。首次部署或版本更新后需要执行迁移：

```bash
docker-compose exec manus-api alembic upgrade head
```

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

# 前端访问测试
curl -I http://localhost:8088

# 数据库连接测试
docker exec manus-postgres pg_isready -U postgres
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
docker-compose restart manus-api

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
docker-compose up -d

# 清理旧镜像
docker image prune -f
```

### 数据库维护

```bash
# 进入数据库
docker exec -it manus-postgres psql -U postgres -d manus

# 执行迁移
docker exec -it manus-api alembic upgrade head

# 备份恢复
docker exec -i manus-postgres psql -U postgres manus < backup.sql
```

---

## 🛠️ 故障排查

### 常见问题

#### 1. 容器启动失败

```bash
# 查看详细日志
docker-compose logs manus-api

# 检查配置文件
docker exec -it manus-api cat /app/.env
docker exec -it manus-api cat /app/config.yaml

# 验证网络连接
docker network inspect manus-network
```

#### 2. 数据库连接失败

```bash
# 检查数据库状态
docker-compose logs manus-postgres

# 测试连接
docker exec manus-postgres pg_isready -U postgres -d manus

# 重置密码（紧急情况）
docker exec -it manus-postgres psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'new_password';"
```

#### 3. 内存不足

```bash
# 查看内存使用
free -h
docker stats --no-stream

# 清理未使用的资源
docker system prune -a --volumes -f

# 调整容器内存限制（编辑 docker-compose.yml）
```

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

### 1. 系统层面

```bash
# 优化内核参数
cat >> /etc/sysctl.conf << 'EOF'
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
vm.swappiness = 10
EOF
sysctl -p

# 禁用 Swap（生产环境推荐）
sudo swapoff -a
sudo sed -i '/ swap / s/^/#/' /etc/fstab
```

### 2. 数据库优化

```bash
# PostgreSQL 调优（根据 16GB 内存）
docker exec manus-postgres bash -c "cat >> /var/lib/postgresql/data/postgresql.conf << 'EOF'
shared_buffers = 4GB
effective_cache_size = 12GB
work_mem = 64MB
maintenance_work_mem = 512MB
max_connections = 100
EOF"

docker-compose restart manus-postgres
```

### 3. Redis 优化

已在 docker-compose.yml 中配置：
- 最大内存：256MB
- 淘汰策略：allkeys-lru
- AOF 持久化：开启

---

## 🔐 HTTPS 配置（可选）

### 使用 Let's Encrypt

```bash
# 安装 Certbot
sudo apt install -y certbot

# 获取证书
sudo certbot certonly --standalone -d your-domain.com

# 配置 Nginx SSL
# 取消 nginx/conf.d/default.conf 中的 SSL 注释
# 修改证书路径为：
# ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
# ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

# 挂载证书到容器
# 在 docker-compose.yml 中添加：
# volumes:
#   - /etc/letsencrypt:/etc/letsencrypt:ro

# 自动续期
sudo crontab -e
# 添加：0 3 1 * * certbot renew --quiet && docker-compose exec manus-nginx nginx -s reload
```

---

## 📝 检查清单

部署前确认：

- [ ] 服务器已安装 Docker 和 Docker Compose
- [ ] 防火墙已配置（仅开放 22、8088 端口）
- [ ] .env 文件已正确配置（数据库密码、COS 凭证、LLM API Key）
- [ ] api/config.yaml 已配置用于初始化默认模型的 LLM 接口
- [ ] 已执行数据库迁移并确认模型、Skill、记忆相关表创建成功
- [ ] 已在设置中心确认默认模型、内置 Skill 和长期记忆配置
- [ ] 已测试数据库连接
- [ ] 已配置日志轮转
- [ ] 已设置自动备份
- [ ] 已验证健康检查端点

---

## 🆘 技术支持

- **项目文档**: README.md
- **API 文档**: http://YOUR_SERVER_IP:8088/docs
- **日志位置**: `docker-compose logs`
- **数据目录**: `/var/lib/docker/volumes`

---

**最后更新时间**: 2026-05-20
**适用版本**: MyManus v1.0  
**部署环境**: Ubuntu 24.04 LTS, 8核/16GB/270GB SSD/18Mbps
