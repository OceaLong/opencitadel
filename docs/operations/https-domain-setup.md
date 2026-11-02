[简体中文](https-domain-setup.zh-CN.md)

# OpenCitadel Domain and HTTPS Setup

> **Prerequisite**: Base deployment is complete and the app is reachable at `http://YOUR_SERVER_IP:8088`.

`make quickstart` derives Nginx hostname, TLS mode and host ports from
`FRONTEND_BASE_URL`. OAuth redirects and secure cookies follow the same origin.
The container then renders its Nginx site configuration. Explicit advanced
overrides remain supported; use this launcher after changing the origin so the
derived environment reaches Compose. **Do not manually edit**
`/etc/nginx/conf.d/` or `docker-compose.yml`.

---

## Checklist

- [ ] Domain A record points to the server IP
- [ ] SSL certificate is ready (Let's Encrypt or commercial; paths below)
- [ ] Firewall allows the selected HTTP port (`8088` for local HTTP, `80` with HTTPS) and `443/tcp` (HTTPS)

---

## 1. Domain only (HTTP)

Edit `.env`:

```bash
cd /opt/opencitadel
vim .env
```

```ini
FRONTEND_BASE_URL=http://your-domain.com:8088
```

Restart Nginx:

```bash
make quickstart
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

Standalone HTTP-01 validation requires public access to host `80/tcp` with no other listener; exposing only `8088/tcp` does not satisfy Certbot validation. Compose otherwise mounts the empty repository directory `deploy/nginx/letsencrypt`; set `LETSENCRYPT_DIR` below to mount host certificates. Rendering configuration does not obtain certificates.

### 2. Update .env

```ini
LETSENCRYPT_DIR=/etc/letsencrypt

# Required when ENV=production (API startup validation)
ENV=production
FRONTEND_BASE_URL=https://your-domain.com
```

For **local HTTP quickstart** only (`make quickstart`), use `ENV=development` with `COOKIE_SECURE=false` and `FRONTEND_BASE_URL=http://localhost:8088` instead.

### 3. Restart Nginx

```bash
make quickstart
```

This also recreates services whose deployment environment changed. The
certificates must already exist. Remove old explicit `COOKIE_SECURE`,
`OAUTH_REDIRECT_BASE`, `HTTPS_ENABLED`, domain or port overrides if you want
them to follow the new origin automatically.

With HTTPS enabled:

- HTTP: `http://your-domain.com` → redirects to `https://your-domain.com`
- HTTPS: `https://your-domain.com`

---

## 3. Verification

```bash
# HTTP redirect (port 80)
curl -I http://your-domain.com

# HTTPS
curl -I https://your-domain.com

# Generated config inside the container
docker compose exec opencitadel-nginx cat /etc/nginx/conf.d/default.conf
docker compose exec opencitadel-nginx nginx -t
```

---

The HTTPS template redirects to `FRONTEND_BASE_URL`, including an explicit
nonstandard HTTPS port. When no origin is supplied, legacy domain/HTTPS-port
overrides remain supported.

## 4. Rollback

Return to HTTP-only access:

```bash
# In .env
ENV=development
FRONTEND_BASE_URL=http://your-domain.com:8088
# Remove old explicit TLS, cookie, OAuth and port overrides to follow the origin.

make quickstart
```

---

## 5. FAQ

### Nginx fails to start: certificate files missing

Confirm `OPENCITADEL_DOMAIN` matches the certificate directory name and files exist under `/etc/letsencrypt/live/${OPENCITADEL_DOMAIN}/`.

### Firewall

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

### WebSocket / SSE issues

Templates include `Upgrade` / `Connection` headers and long timeouts; extra Nginx changes are usually unnecessary. If problems persist, verify first-class Inference and Integration resource URLs use HTTPS.

---

**Last updated**: 2026-10-10
**Applies to**: OpenCitadel v1.0
