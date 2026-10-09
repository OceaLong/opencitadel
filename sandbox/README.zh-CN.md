[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel 沙箱服务

基于 Ubuntu 22.04 构建的沙箱环境，提供隔离的代码执行、浏览器自动化和远程桌面访问能力。

## 技术栈

- Ubuntu 22.04
- Python 3.12 + FastAPI（依赖管理：uv）
- Node.js 24 (LTS)
- Chromium（浏览器自动化）
- Xvfb + x11vnc + websockify（虚拟显示 + VNC）
- Supervisor（进程管理）

## 架构

沙箱通过 Supervisor 管理多个进程：

| 进程       | 端口         | 说明                             |
| ---------- | ------------ | -------------------------------- |
| FastAPI    | 8080         | REST API（文件操作、Shell 执行） |
| Chrome     | 8222（内部） | 浏览器实例                       |
| socat      | 9222         | Chrome DevTools Protocol 代理    |
| Xvfb       | -            | 虚拟显示器 (:1)                  |
| x11vnc     | 5900         | VNC 服务                         |
| websockify | 5901         | WebSocket VNC 代理               |

## API 接口

| 方法 | 路径                           | 说明            |
| ---- | ------------------------------ | --------------- |
| POST | `/api/file/read-file`          | 读取文件        |
| POST | `/api/file/write-file`         | 写入文件        |
| POST | `/api/file/upload-file`        | 上传文件        |
| GET  | `/api/file/download-file`      | 下载文件        |
| POST | `/api/shell/exec-command`      | 执行命令        |
| POST | `/api/shell/read-shell-output` | 读取 Shell 输出 |
| GET  | `/api/supervisor/status`       | 获取进程状态    |

所有 `/api/*` 都要求 `Authorization: Bearer <SANDBOX_ACCESS_TOKEN>`；未配置 Token 返回 503，错误或缺失 Header 返回 401。动态沙箱由 API/内核通过 `HMAC(SANDBOX_TOKEN_SEED, sandbox_id)` 派生并注入独立 Token；Seed 不进入沙箱。`/openapi.json` 位于该前缀之外。CDP/VNC 端口没有此 HTTP 中间件，应保持内部网络与 API 代理边界。

### 路径 containment 与 sudo 转义

`FileService` 会将每个文件/目录路径与
`SANDBOX_ALLOWED_ROOTS = ("/home/ubuntu", "/tmp", "/workspace")`
（`app/services/file.py`）做校验。用 `os.path.realpath` 解析符号链接与
`..` 分量后，任何落在这些根目录之外的路径都会被拒绝，返回
`path outside sandbox allowed roots`（`app/services/file.py`）——与
API 侧 `source_validator.py` 相同的 `realpath` + `commonpath` 手法。
sudo 提权的文件读写通过 `sudo cat` / `sudo tee` 走 Shell，目标路径与任何
临时文件都经过 `shlex.quote()` 转义，避免恶意路径通过嵌套引号从命令字符串
中逃逸。相关测试见 `tests/test_file_path_containment.py`、
`tests/test_file_endpoints.py`、`tests/test_shell_service.py`。

## 本地开发

### 环境准备

```bash
pip install uv
uv sync --frozen
```

### 启动服务

在容器内或本地：

```bash
SANDBOX_ACCESS_TOKEN=replace-with-a-strong-local-token \
  uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## Docker 部署

沙箱服务通过根目录的 `docker-compose.yml` 统一部署。Dockerfile 在 `UV_INDEX_URL` 生效后执行 `uv sync --frozen`，依赖安装到 `/venv`，运行时通过 `PATH=/venv/bin` 解析 `uvicorn`。

`pip install uv` 与 `uv sync` 使用可覆盖的 build args（默认阿里云 PyPI、`UV_VERSION=0.11.33`、`UV_HTTP_TIMEOUT=300`）。npm 默认 `registry.npmmirror.com`。

```bash
docker compose build opencitadel-sandbox
```

默认生产路径使用动态沙箱。Deployment Settings 选择 Docker/Kubernetes Driver、镜像、网络、代理与 Namespace；活动 Operations Policy 在每次认证创建请求中携带 TTL 与资源限制。固定容器仅用于 `docker compose --profile fixed-sandbox` 或显式部署的外部沙箱服务。

镜像以 `ubuntu` 非 Root 用户运行，不授予 sudo 权限。当前 `fixed-sandbox` Compose Profile 没有注入 `SANDBOX_ACCESS_TOKEN`，所以 `/openapi.json` 健康检查可成功而数据面 API 仍返回 503；如需外部固定沙箱，必须显式注入 Token 并配置一致的调用方凭据。

### 超时配置

沙箱闲置自动销毁时间通过环境变量配置（单位：分钟）：

```bash
SERVER_TIMEOUT_MINUTES=60   # 推荐（pydantic-settings 标准名）
```

执行内核从当前 Operations Policy 的 `ttl_minutes` 注入 `SERVER_TIMEOUT_MINUTES`；这不是 `SANDBOX_TTL_MINUTES` 环境变量覆盖项。

### 端口说明

在 Docker Compose 部署中，沙箱端口仅在容器网络内部可访问，不对外暴露：

- `8080` — FastAPI REST API
- `9222` — Chrome DevTools Protocol
- `5900` — VNC RFB
- `5901` — WebSocket VNC（API 服务通过此端口代理 VNC 到前端）
