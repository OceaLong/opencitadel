[简体中文](https-domain-setup.zh-CN.md)

# OpenCitadel Domain and HTTPS Setup

> **Prerequisite**: Base deployment is complete and the app is reachable at `http://YOUR_SERVER_IP:8088`.

Nginx site configuration is generated automatically at container startup from `.env`. **Do not manually edit** `nginx/conf.d/` or `docker-compose.yml`.

---

## Checklist

- [ ] Domain A record points to the server IP
- [ ] SSL certificate is ready (Let's Encrypt or commercial; paths below)
- [ ] Firewall allows `8088/tcp` (HTTP) and `443/tcp` (HTTPS)

---

## 1. Domain only (HTTP)

Edit `.env`:

```bash
cd /opt/opencitadel
vim .env
```

```ini
OPENCITADEL_DOMAIN=your-domain.com
HTTPS_ENABLED=false
NGINX_PORT=8088
```

Restart Nginx:

```bash
docker compose up -d opencitadel-nginx
```

Visit: `http://your-domain.com:8088`

---

## 2. Enable HTTPS

### 1. Prepare certificates

Default certificate paths on the host:

- `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/fullchain.pem`
- `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/privkey.pem`

Certbot example (standalone mode requires temporarily stopping Nginx):

```bash
sudo apt install -y certbot
docker compose stop opencitadel-nginx
sudo certbot certonly --standalone \
  -d your-domain.com \
  --email your-email@example.com \
  --agree-tos \
  --non-interactive
```

Certificate renewal is your responsibility; after renewal run `docker compose restart opencitadel-nginx`.

### 2. Update .env

```ini
OPENCITADEL_DOMAIN=your-domain.com
HTTPS_ENABLED=true
NGINX_PORT=8088
NGINX_HTTPS_PORT=443

# Required when ENV=production (API startup validation)
ENV=production
COOKIE_SECURE=true
FRONTEND_BASE_URL=https://your-domain.com
OAUTH_REDIRECT_BASE=https://your-domain.com/api/auth/oauth
USE_DB_APP_CONFIG=true
```

For **local HTTP quickstart** only (`make quickstart`), use `ENV=development` with `COOKIE_SECURE=false` and `FRONTEND_BASE_URL=http://localhost:8088` instead.

### 3. Restart Nginx

```bash
docker compose up -d opencitadel-nginx
```

With HTTPS enabled:

- HTTP: `http://your-domain.com:8088` → redirects to `https://your-domain.com`
- HTTPS: `https://your-domain.com`

---

## 3. Verification

```bash
# HTTP redirect (port 8088)
curl -I http://your-domain.com:8088

# HTTPS
curl -I https://your-domain.com

# Generated config inside the container
docker compose exec opencitadel-nginx cat /etc/nginx/conf.d/default.conf
docker compose exec opencitadel-nginx nginx -t
```

---

## 4. Rollback

Return to HTTP-only access:

```bash
# In .env
HTTPS_ENABLED=false

docker compose up -d opencitadel-nginx
```

---

## 5. FAQ

### Nginx fails to start: certificate files missing

Confirm `OPENCITADEL_DOMAIN` matches the certificate directory name and files exist under `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/`.

### Firewall

```bash
sudo ufw allow 8088/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

### WebSocket / SSE issues

Templates include `Upgrade` / `Connection` headers and long timeouts; extra Nginx changes are usually unnecessary. If problems persist, check external service URLs in `api/config.yaml` use HTTPS.

---

**Last updated**: 2026-06-15  
**Applies to**: OpenCitadel v1.0
