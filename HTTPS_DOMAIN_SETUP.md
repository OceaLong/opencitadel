# MyManus 域名与 HTTPS 配置指南

> **前提条件**：已完成基础部署，服务正常运行在 `http://YOUR_SERVER_IP:8088`

Nginx 站点配置由容器启动时根据 `.env` 自动生成，**无需手动编辑** `nginx/conf.d/` 或 `docker-compose.yml`。

---

## 配置清单

- [ ] 域名已解析到服务器 IP（A 记录）
- [ ] SSL 证书已准备（Let's Encrypt 或商业证书，路径见下文）
- [ ] 防火墙已开放 `8088/tcp`（HTTP）与 `443/tcp`（HTTPS）

---

## 一、仅配置域名（HTTP）

编辑 `.env`：

```bash
cd /opt/my-manus
vim .env
```

```ini
MANUS_DOMAIN=your-domain.com
HTTPS_ENABLED=false
NGINX_PORT=8088
```

重启 Nginx：

```bash
docker compose up -d manus-nginx
```

访问：`http://your-domain.com:8088`

---

## 二、启用 HTTPS

### 1. 准备证书

默认约定证书位于宿主机：

- `/etc/letsencrypt/live/${MANUS_DOMAIN}/fullchain.pem`
- `/etc/letsencrypt/live/${MANUS_DOMAIN}/privkey.pem`

使用 Certbot 获取证书示例（standalone 模式需临时停止 Nginx）：

```bash
sudo apt install -y certbot
docker compose stop manus-nginx
sudo certbot certonly --standalone \
  -d your-domain.com \
  --email your-email@example.com \
  --agree-tos \
  --non-interactive
```

证书续期由部署者自行维护；续期后执行 `docker compose restart manus-nginx` 即可。

### 2. 修改 .env

```ini
MANUS_DOMAIN=your-domain.com
HTTPS_ENABLED=true
NGINX_PORT=8088
NGINX_HTTPS_PORT=443
```

### 3. 重启 Nginx

```bash
docker compose up -d manus-nginx
```

启用 HTTPS 后：

- HTTP：`http://your-domain.com:8088` → 自动跳转至 `https://your-domain.com`
- HTTPS：`https://your-domain.com`

---

## 三、验证

```bash
# HTTP 重定向（8088 端口）
curl -I http://your-domain.com:8088

# HTTPS
curl -I https://your-domain.com

# 检查容器内生成的配置
docker compose exec manus-nginx cat /etc/nginx/conf.d/default.conf
docker compose exec manus-nginx nginx -t
```

---

## 四、回滚

恢复纯 HTTP 访问：

```bash
# .env 中设置
HTTPS_ENABLED=false

docker compose up -d manus-nginx
```

---

## 五、常见问题

### Nginx 启动失败：证书文件不存在

确认 `MANUS_DOMAIN` 与证书目录名一致，且文件存在于 `/etc/letsencrypt/live/${MANUS_DOMAIN}/`。

### 防火墙

```bash
sudo ufw allow 8088/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

### WebSocket / SSE 异常

模板已包含 `Upgrade` / `Connection` 头与长超时，一般无需额外修改。若仍有问题，检查 `api/config.yaml` 中外部服务 URL 是否使用 HTTPS。

---

**最后更新**：2026-06-15  
**适用版本**：MyManus v1.0
