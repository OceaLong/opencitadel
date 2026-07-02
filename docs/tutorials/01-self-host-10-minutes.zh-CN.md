[English](01-self-host-10-minutes.md)

# 教程 1：10 分钟自托管 OpenCitadel

本指南帮助你在零基础上完成 **OpenCitadel** 的首次 Agent 任务，使用 **自带 API Key**（OpenAI、Anthropic 或任意 OpenAI 兼容提供商）。

## 前置条件

- Docker Desktop 或 Docker Engine + Compose v2
- 至少 8 GB 内存（推荐 16 GB）
- 来自模型提供商的 LLM API Key

## 步骤

### 1. 克隆并配置

```bash
git clone https://github.com/OceaLong/opencitadel.git
cd opencitadel
make quickstart
```

脚本会将 `.env.example` 复制为 `.env`，生成密钥，并提示你设置 `BOOTSTRAP_ADMIN_PASSWORD`。

### 2. 启动服务栈

`make quickstart` 会执行 `docker compose up -d --build`。首次构建可能需要 5–10 分钟。

健康检查通过后，打开 **http://localhost:8088**。

### 3. 登录

- 邮箱：`BOOTSTRAP_ADMIN_EMAIL` 的值（默认 `admin@example.com`）
- 密码：你设置的 `BOOTSTRAP_ADMIN_PASSWORD`

### 4. 添加模型

1. 打开 **Settings → Models**
2. 点击 **Add model**
3. 选择提供商（如 OpenAI），粘贴 API Key，选择模型名称
4. 设为默认模型

### 5. 运行首个任务

在首页尝试：

> Summarize the top 3 trends in enterprise AI agents in 2026 and save a brief report as report.md

观察 Agent 规划、在沙箱中使用工具，并实时流式输出结果。

## 完全离线（可选）

适用于气隙或纯本地部署：

```bash
# 在 .env 中
COMPOSE_PROFILES=local
STORAGE_PROVIDER=minio
COOKIE_SECURE=false
FRONTEND_BASE_URL=http://localhost:8088
```

在宿主机安装 [Ollama](https://ollama.com)，拉取能力足够的模型（如 `qwen2.5:14b`），然后在 Settings 中添加，Base URL 设为 `http://host.docker.internal:11434/v1`。

**注意：** 较小的本地模型可能难以完成多步 Agent 任务。自带云端 API Key 能获得最佳首次体验。

## 故障排查

| 问题 | 解决方法 |
|------|----------|
| 登录 502 | 等待 `opencitadel-migrate` 完成；查看 `docker compose logs opencitadel-migrate` |
| Agent 无响应 | 确认已设置带有效 API Key 的默认模型 |
| OOM / 运行缓慢 | 参见 [部署指南](../operations/deployment.zh-CN.md) 内存调优；在小 VM 上启用 swap |

## 下一步

- [教程 2：内部知识库](./02-internal-knowledge-base.zh-CN.md)
- [部署指南](../operations/deployment.zh-CN.md)
