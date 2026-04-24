# MyManus 域名与 HTTPS 配置指南

> **前提条件**：已完成基础部署，服务正常运行在 `http://YOUR_SERVER_IP:8088`

---

## 📋 配置清单

- [ ] 域名已解析到服务器 IP（A 记录）
- [ ] SSL 证书已获取（Let's Encrypt 或商业证书）
- [ ] 防火墙已开放 443 端口

---

## 一、配置域名

### 1. 修改 Nginx 配置

编辑 `nginx/conf.d/default.conf`：

```bash
cd /opt/my-manus
vim nginx/conf.d/default.conf
```

**修改前（第 33 行）：**
```nginx
server_name _;
```

**修改后：**
```nginx
server_name your-domain.com www.your-domain.com;
```

> 将 `your-domain.com` 替换为你的实际域名

### 2. 重启 Nginx

```bash
docker-compose restart manus-nginx
```

### 3. 验证域名访问

浏览器访问：`http://your-domain.com:8088`

---

## 二、启用 HTTPS（推荐 Let's Encrypt）

### 方案 A：使用 Certbot（自动化）

#### 1. 安装 Certbot

```bash
sudo apt update
sudo apt install -y certbot
```

#### 2. 临时停止 Nginx（ standalone 模式需要）

```bash
docker-compose stop manus-nginx
```

#### 3. 获取 SSL 证书

```bash
sudo certbot certonly --standalone \
  -d your-domain.com \
  -d www.your-domain.com \
  --email your-email@example.com \
  --agree-tos \
  --non-interactive
```

证书文件位置：
- 证书链：`/etc/letsencrypt/live/your-domain.com/fullchain.pem`
- 私钥：`/etc/letsencrypt/live/your-domain.com/privkey.pem`

#### 4. 启动 Nginx 并挂载证书

编辑 `docker-compose.yml`（第 111-115 行），取消注释 SSL 相关配置：

```yaml
manus-nginx:
  image: nginx:alpine
  container_name: manus-nginx
  restart: unless-stopped
  ports:
    - "${NGINX_PORT:-8088}:80"
    - "443:443"  # 取消注释
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx/conf.d:/etc/nginx/conf.d:ro
    - /etc/letsencrypt:/etc/letsencrypt:ro  # 取消注释
  depends_on:
    manus-ui:
      condition: service_healthy
    manus-api:
      condition: service_healthy
  networks:
    - manus-network
```

#### 5. 启用 Nginx SSL 配置

编辑 `nginx/conf.d/default.conf`，**替换整个文件内容**为：

```nginx
# HTTP -> HTTPS 重定向
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;
    return 301 https://$server_name$request_uri;
}

upstream ui_backend {
    server manus-ui:3000;
}

upstream api_backend {
    server manus-api:8000;
}

# HTTPS 服务
server {
    listen 443 ssl;
    server_name your-domain.com www.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    # API 接口代理（含 WebSocket / SSE 支持）
    location /api/ {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;

        # WebSocket 支持
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        # 标准代理头
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE / 长连接支持
        proxy_buffering off;
        proxy_cache     off;

        # 超时设置
        proxy_read_timeout    86400s;
        proxy_send_timeout    86400s;
        proxy_connect_timeout 60s;
    }

    # 前端 UI 代理
    location / {
        proxy_pass http://ui_backend;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Next.js HMR WebSocket
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

> **注意**：将所有 `your-domain.com` 替换为你的实际域名

#### 6. 重新部署

```bash
docker-compose up -d
```

#### 7. 配置自动续期

```bash
sudo crontab -e
```

添加以下内容（每月 1 号凌晨 3 点检查续期）：

```cron
0 3 1 * * certbot renew --quiet && docker-compose exec manus-nginx nginx -s reload
```

---

### 方案 B：使用商业证书（手动）

#### 1. 上传证书文件

将证书文件复制到服务器：

```bash
mkdir -p /opt/my-manus/nginx/ssl
cp fullchain.pem /opt/my-manus/nginx/ssl/
cp privkey.pem /opt/my-manus/nginx/ssl/
chmod 600 /opt/my-manus/nginx/ssl/*.pem
```

#### 2. 修改 docker-compose.yml

```yaml
manus-nginx:
  # ... 其他配置 ...
  ports:
    - "${NGINX_PORT:-8088}:80"
    - "443:443"
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx/conf.d:/etc/nginx/conf.d:ro
    # 直接挂载系统 Let's Encrypt 证书目录
    - /etc/letsencrypt:/etc/letsencrypt:ro
```

#### 3. 修改 Nginx 配置

编辑 `nginx/conf.d/default.conf`，证书路径改为：

```nginx
ssl_certificate     /etc/nginx/ssl/fullchain.pem;
ssl_certificate_key /etc/nginx/ssl/privkey.pem;
```

#### 4. 重新部署

```bash
docker-compose up -d
```

---

## 三、更新环境变量（可选）

如果使用了 COS 等外部服务，更新 `.env` 中的域名配置：

```bash
vim .env
```

```ini
# 腾讯云 COS 配置（如果使用自定义域名）
COS_DOMAIN=https://your-cdn-domain.com
```

重新加载配置：

```bash
docker-compose restart manus-api
```

---

## 四、验证 HTTPS

### 1. 浏览器访问

```
https://your-domain.com
```

应自动从 HTTP 重定向到 HTTPS，浏览器显示安全锁图标。

### 2. 命令行测试

```bash
# 测试 HTTP 重定向
curl -I http://your-domain.com

# 测试 HTTPS
curl -I https://your-domain.com

# 检查证书有效期
echo | openssl s_client -connect your-domain.com:443 2>/dev/null | openssl x509 -noout -dates
```

### 3. 在线检测工具

访问以下网站检测 SSL 配置：
- https://www.ssllabs.com/ssltest/
- https://myssl.com/

---

## 五、常见问题

### 1. 证书续期失败

```bash
# 手动续期测试
sudo certbot renew --dry-run

# 查看续期日志
sudo cat /var/log/letsencrypt/letsencrypt.log
```

### 2. 混合内容警告

确保所有资源使用 HTTPS：
- 检查前端代码中是否有硬编码的 `http://` URL
- 更新 `api/config.yaml` 中的外部服务地址为 HTTPS

### 3. WebSocket 连接失败

确认 Nginx 配置中包含：
```nginx
proxy_set_header Upgrade    $http_upgrade;
proxy_set_header Connection $connection_upgrade;
```

### 4. 防火墙阻止 443 端口

```bash
sudo ufw allow 443/tcp
sudo ufw reload
```

---

## 六、回滚方案

如需恢复 HTTP 访问：

```bash
# 1. 恢复原始 Nginx 配置
cd /opt/my-manus
git checkout nginx/conf.d/default.conf

# 2. 注释 docker-compose.yml 中的 443 端口和证书挂载
vim docker-compose.yml

# 3. 重新部署
docker-compose up -d
```

---

**最后更新**：2026-04-23  
**适用版本**：MyManus v1.0
