[English](01-self-host-10-minutes.md)

# 教程 1：自托管 OpenCitadel

本指南帮助你在零基础上完成 **OpenCitadel** 的首次 Agent 任务，使用 **自带 API Key**（OpenAI、Anthropic 或任意 OpenAI 兼容提供商）。

## 前置条件

- 已运行的 Docker Desktop 或 Docker Engine + Compose v2（`docker info` 必须成功）
- 至少 8 GB 内存（推荐 16 GB）
- 首个 Agent 任务需要可访问的推理端点，可在启动后配置

## 步骤

### 1. 克隆并配置

```bash
git clone https://github.com/OceaLong/opencitadel.git
cd opencitadel
make quickstart
```

首次运行创建私有 `.env`，自动生成独立随机密钥与管理员密码并启动服务栈。
就绪检查通过后显示初始登录信息；再次运行保留已有配置与凭据，无需交互输入。
只生成配置、不启动容器可运行 `make configure`；可选覆盖分类见
[`deploy/config/.env.reference`](../../deploy/config/.env.reference)。

> **仅用于本地体验：** quickstart 会主动设置 `ENV=development`、
> 内置 MinIO 与 localhost HTTP Origin，自动推导 `COOKIE_SECURE=false`。不要把该 `.env`
> 暴露或直接提升到公网/多用户环境。生产部署前须完整执行[生产部署
> 指南](../operations/deployment.zh-CN.md)中的 Secret、数据库角色、
> Redis、可信代理、出站策略与验证流程。

### 2. 启动服务栈

`make quickstart` 会依次执行：

1. `docker compose build opencitadel-sandbox` — 动态沙箱所需镜像（compose 中该服务在 `fixed-sandbox` profile 下，默认不启动，但执行内核创建的沙箱依赖此镜像）
2. `docker compose up -d --build --wait --wait-timeout 300` — 启动 API、执行内核、UI、Postgres、Redis，以及（quickstart 默认）MinIO，然后等待服务健康与迁移成功

无缓存的首次构建会下载基础镜像、浏览器/系统包及语言依赖，耗时取决于网络与已有缓存，
可能超过十分钟；后续启动复用这些镜像层。

健康检查通过后，打开 **http://localhost:8088**。

> **对象存储默认**：quickstart 开箱即用内置 MinIO 存储。如需腾讯云 COS 或其他存储配置，见[部署指南 — 部署模式](../operations/deployment.zh-CN.md)。

### 3. 登录

- 邮箱：`BOOTSTRAP_ADMIN_EMAIL` 的值（默认 `admin@example.com`）
- 密码：首次启动后显示的自动生成值（保存在私有 `.env` 的 `BOOTSTRAP_ADMIN_PASSWORD`）

### 4. 配置推理

推理配置是显式三层结构：**Endpoint** 持有 Provider 与 Credential，类型化 **Model**
隶属于 Endpoint，**Binding** 为每个用途选择 Model。完整说明见
[推理控制面](../architecture/inference-control-plane.zh-CN.md)。

1. 打开 **设置 → 推理**
2. 点击 **Add endpoint** — 选择 Provider、Base URL、粘贴 API Key
3. 在该 Endpoint 下点击 **Add model** — 填写模型名并选择 `chat`
4. 将 `chat` 用途绑定到该 Model

首次使用只需完成以上配置。新安装的知识库默认文本提取与关键词检索，Embedding、重排、
GraphRAG 和 OCR 均为可选；需要时配置模型并在运行时策略中开启，步骤见
[知识库教程](02-internal-knowledge-base.zh-CN.md)。升级保留已有策略。

### 5. 运行首个任务

在首页尝试：

> Summarize the top 3 trends in enterprise AI agents in 2026 and save a brief report as report.md

观察 Agent 规划、在沙箱中使用工具，并实时流式输出结果。

## 本地推理（可选）

使用本地推理时，在 `.env` 中设置：

```bash
OUTBOUND_PRIVATE_HOST_ALLOWLIST=host.docker.internal
```

安装 [Ollama](https://ollama.com)，拉取模型，然后在设置 → 推理中添加 **Endpoint**
（`http://host.docker.internal:11434/v1`）、Chat **Model** 与 `chat` **Binding**。
保留精确白名单，不要使用通配符。完整本地模式说明见
[部署指南 — local 模式](../operations/deployment.zh-CN.md)。

气隙安装需要在断网前备齐容器镜像、模型权重与依赖，并使用不依赖远程来源的任务。
本地推理本身不会让浏览器和集成离线。Agent 工作流所选模型必须支持工具调用。

## 故障排查

| 问题           | 解决方法                                                                         |
| -------------- | -------------------------------------------------------------------------------- |
| 登录 502       | 等待 `opencitadel-migrate` 完成；查看 `docker compose logs opencitadel-migrate`  |
| Agent 无响应   | 确认有效 `chat` Binding 能解析到可访问 Model 与 Endpoint Credential              |
| OOM / 运行缓慢 | 参见 [部署指南](../operations/deployment.zh-CN.md) 内存调优；在小 VM 上启用 swap |

## 下一步

- [教程 2：内部知识库](./02-internal-knowledge-base.zh-CN.md)
- [部署指南](../operations/deployment.zh-CN.md)
