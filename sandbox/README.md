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

### Path containment and sudo escaping

`FileService` normalizes every file/directory path against
`SANDBOX_ALLOWED_ROOTS = ("/home/ubuntu", "/tmp", "/workspace")`
(`app/services/file.py:35`). After resolving symlinks and `..` components
with `os.path.realpath`, any path that lands outside those roots is rejected
with `path outside sandbox allowed roots` (`app/services/file.py:75,99`) —
the same `realpath` + `commonpath` technique the API side uses in
`source_validator.py`. Sudo-elevated file reads/writes shell out through
`sudo cat` / `sudo tee`, with both the target path and any temp file passed
through `shlex.quote()` so a malicious path cannot break out of the command
string via nested quoting. Covered by `tests/test_file_path_containment.py`,
`tests/test_file_endpoints.py`, and `tests/test_shell_service.py`.

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

`pip install uv` and `uv sync` use overridable build args (default Aliyun PyPI, `UV_VERSION=0.11.33`, `UV_HTTP_TIMEOUT=300`). npm defaults to `registry.npmmirror.com`.

```bash
docker compose build opencitadel-sandbox
```

The default production path uses dynamic sandboxes. Deployment Settings choose the Docker or Kubernetes driver, image, network, proxy, and namespace; the active Operations Policy supplies TTL and resource limits for each authenticated create request. Fixed containers are available only through `docker compose --profile fixed-sandbox` or an explicitly deployed external sandbox service.

### Timeout Configuration

Sandbox idle destroy timeout (minutes):

```bash
SERVER_TIMEOUT_MINUTES=60   # Recommended (pydantic-settings standard)
```

The execution kernel injects `SERVER_TIMEOUT_MINUTES` via `SANDBOX_TTL_MINUTES` when creating dynamic sandboxes.

### Ports

In Docker Compose, sandbox ports are internal only:

- `8080` — FastAPI REST API
- `9222` — Chrome DevTools Protocol
- `5900` — VNC RFB
- `5901` — WebSocket VNC (API proxies VNC to frontend)
