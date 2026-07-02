[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel Sandbox Service

Ubuntu 22.04-based isolated environment for code execution, browser automation, and remote desktop access.

## Tech Stack

- Ubuntu 22.04
- Python 3.10 + FastAPI (uv for dependency management)
- Node.js 24 (LTS)
- Chromium (browser automation)
- Xvfb + x11vnc + websockify (virtual display + VNC)
- Supervisor (process management)

## Architecture

Supervisor manages multiple processes:

| Process | Port | Description |
|---------|------|-------------|
| FastAPI | 8080 | REST API (files, Shell execution) |
| Chrome | 8222 (internal) | Browser instance |
| socat | 9222 | Chrome DevTools Protocol proxy |
| Xvfb | — | Virtual display (:1) |
| x11vnc | 5900 | VNC server |
| websockify | 5901 | WebSocket VNC proxy |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/file/read-file` | Read file |
| POST | `/api/file/write-file` | Write file |
| POST | `/api/file/upload-file` | Upload file |
| GET | `/api/file/download-file` | Download file |
| POST | `/api/shell/exec-command` | Execute command |
| POST | `/api/shell/read-shell-output` | Read Shell output |
| GET | `/api/supervisor/status` | Process status |

## Local Development

### Prerequisites

```bash
pip install uv
uv sync --frozen
```

### Start Service

Inside a container or locally:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## Docker Deployment

Sandbox deploys via root `docker-compose.yml`. After `UV_INDEX_URL` is set, Dockerfile runs `uv sync --frozen`; dependencies install to `/venv` with `PATH=/venv/bin` for `uvicorn`.

`pip install uv` and `uv sync` use overridable build args (default Aliyun PyPI, `UV_VERSION=0.11.19`, `UV_HTTP_TIMEOUT=300`). npm defaults to `registry.npmmirror.com`.

```bash
docker compose build opencitadel-sandbox
```

Default production path is dynamic sandboxes: when `sandbox.address: null` in `api/config.yaml`, Worker creates `opencitadel-sandbox-*` instances via Docker/Kubernetes driver. Fixed containers are for `docker compose --profile fixed-sandbox` or external sandbox clusters, connected via `sandbox.address`.

### Timeout Configuration

Sandbox idle destroy timeout (minutes):

```bash
SERVER_TIMEOUT_MINUTES=60   # Recommended (pydantic-settings standard)
# Legacy alias: SERVICE_TIMEOUT_MINUTES=60
```

API/Worker inject `SERVER_TIMEOUT_MINUTES` via `SANDBOX_TTL_MINUTES` when creating dynamic sandboxes.

### Ports

In Docker Compose, sandbox ports are internal only:

- `8080` — FastAPI REST API
- `9222` — Chrome DevTools Protocol
- `5900` — VNC RFB
- `5901` — WebSocket VNC (API proxies VNC to frontend)
