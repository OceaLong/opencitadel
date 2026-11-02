[English](https-domain-setup.md)

# OpenCitadel 域名与 HTTPS 配置指南

> **前提条件**：已完成基础部署，服务正常运行在 `http://YOUR_SERVER_IP:8088`

`make quickstart` 从 `FRONTEND_BASE_URL` 推导 Nginx 域名、TLS 模式与宿主端口，
OAuth 回调及 Cookie 安全属性也跟随该 Origin。容器再渲染 Nginx 站点配置。高级显式覆盖
继续有效；修改 Origin 后使用该启动命令，使推导的环境参数传入 Compose。
**无需手动编辑** `/etc/nginx/conf.d/` 或 `docker-compose.yml`。

---

## 配置清单

- [ ] 域名已解析到服务器 IP（A 记录）
- [ ] SSL 证书已准备（Let's Encrypt 或商业证书，路径见下文）
- [ ] 防火墙开放所选 HTTP 端口（本地 HTTP 为 `8088`，HTTPS 的重定向为 `80`）与 `443/tcp`（HTTPS）

---

## 一、仅配置域名（HTTP）

编辑 `.env`：

```bash
cd /opt/opencitadel
vim .env
```

```ini
FRONTEND_BASE_URL=http://your-domain.com:8088
```

重启 Nginx：

```bash
make quickstart
```

访问：`http://your-domain.com:8088`

---

## 二、启用 HTTPS

### 1. 准备证书

默认约定证书位于宿主机：

- `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/fullchain.pem`
- `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/privkey.pem`

使用 Certbot 获取证书示例（standalone 模式需临时停止 Nginx）：

```bash
sudo apt install -y certbot
docker compose stop opencitadel-nginx
sudo certbot certonly --standalone \
  -d your-domain.com \
  --email your-email@example.com \
  --agree-tos \
  --non-interactive
```

证书续期由部署者自行维护；续期后执行 `docker compose restart opencitadel-nginx` 即可。

HTTP-01 standalone 校验需要宿主 `80/tcp` 可从公网到达，且无其他进程占用；只开放 `8088/tcp` 不满足 Certbot 校验。默认 Compose 挂载仓库内空目录 `deploy/nginx/letsencrypt`，必须设置下面的 `LETSENCRYPT_DIR` 才能使用宿主证书。证书必须已有完整链与私钥；生成配置不会申请证书。

### 2. 修改 .env

```ini
LETSENCRYPT_DIR=/etc/letsencrypt

# ENV=production 时 API 启动校验必填
ENV=production
FRONTEND_BASE_URL=https://your-domain.com
```

**本地 HTTP 快速体验**（`make quickstart`）请使用 `ENV=development`、`COOKIE_SECURE=false`、`FRONTEND_BASE_URL=http://localhost:8088`。

### 3. 重启 Nginx

```bash
make quickstart
```

该命令也会重建部署环境发生变化的服务，证书须事先存在。如果希望旧配置自动跟随新的
Origin，移除已有显式 `COOKIE_SECURE`、`OAUTH_REDIRECT_BASE`、`HTTPS_ENABLED`、域名
或端口覆盖。

启用 HTTPS 后：

- HTTP：`http://your-domain.com` → 自动跳转至 `https://your-domain.com`
- HTTPS：`https://your-domain.com`

---

## 三、验证

```bash
# HTTP 重定向（80 端口）
curl -I http://your-domain.com

# HTTPS
curl -I https://your-domain.com

# 检查容器内生成的配置
docker compose exec opencitadel-nginx cat /etc/nginx/conf.d/default.conf
docker compose exec opencitadel-nginx nginx -t
```

---

HTTPS 模板重定向到 `FRONTEND_BASE_URL`，保留显式非标准 HTTPS 端口；未提供 Origin 时，
仍支持原域名/HTTPS 端口覆盖。

## 四、回滚

恢复纯 HTTP 访问：

```bash
# .env 中设置
ENV=development
FRONTEND_BASE_URL=http://your-domain.com:8088
# 移除已有显式 HTTPS_ENABLED、Cookie、OAuth 与端口覆盖，让它们跟随 Origin。

make quickstart
```

---

## 五、常见问题

### Nginx 启动失败：证书文件不存在

确认 `OPENCITADEL_DOMAIN` 与证书目录名一致，且文件存在于 `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/`。

### 防火墙

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

### WebSocket / SSE 异常

模板已包含 `Upgrade` / `Connection` 头与长超时，一般无需额外修改。若仍有问题，检查一等 Inference 与 Integration Resource URL 是否使用 HTTPS。

---

**最后更新**：2026-10-08
**适用版本**：OpenCitadel v1.0
