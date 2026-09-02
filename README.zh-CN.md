# OpenCitadel v2

[English](README.md)

OpenCitadel 是以单一持久化执行内核为中心的私有化 Agent 运行平台。v2 是一次
有意不兼容的切换：从空 PostgreSQL 数据库开始，只保留 Agent Run、审批、知识、
推理配置、MCP 工具、团队、治理、配额、审计与通知。

## 内核变化

- PostgreSQL 命令和追加式事件是唯一工作流事实源。
- 纯 Reducer 推导状态；所有 Projection 都可以删除后重建。
- 外部工作严格收敛为五类持久 Effect：`model.call`、
  `knowledge.retrieve`、`tool.call`、`file.operation`、`knowledge.build`。
- 每个 Effect 都有幂等键、硬超时、有界重试和持久化结果。
- 审批会冻结评审人集合，并收敛到批准、拒绝、过期、取消或错误。
- Docker 与 Kubernetes 都按 Run 创建隔离且资源受限的沙箱。
- 签名 PostgreSQL 授权上下文与强制 RLS 保护所有租户数据表。

## 核心产品

| 领域 | UI | API 根路径 |
| --- | --- | --- |
| Agent Run | `/`、`/runs/[id]` | `/api/runs` |
| 审批收件箱 | `/approvals` | `/api/approvals` |
| 文件与知识 | `/knowledge` | `/api/files`、`/api/knowledge-bases` |
| 推理与 MCP | `/settings` | `/api/inference`、`/api/integrations/mcp` |
| 团队 | `/teams` | `/api/teams`、`/api/invitations` |
| 管理 | `/admin` | `/api/admin`、`/api/governance-policy` |

## 快速开始

```bash
make quickstart
```

脚本会创建含独立本地密钥的 `.env`、构建沙箱镜像、执行单向绿色迁移，并在
`http://localhost:8088` 启动服务。如需先明确清空本地应用数据：

```bash
bash scripts/quickstart.sh --reset-data
```

登录后在设置中配置 OpenAI 兼容 Endpoint、Model 和 Binding，即可创建 Run。
本地文件存储默认使用 MinIO。

## 开发

```bash
cd api && uv sync --all-groups && uv run pytest -q
cd ui && npm install && npm run typecheck && npm test
cd sandbox && uv sync && uv run pytest -q
```

更多内容见[内核架构](docs/architecture/kernel-v2.zh-CN.md)、
[部署](docs/operations/deployment.zh-CN.md)、[API](api/README.zh-CN.md) 和
[UI](ui/README.zh-CN.md)。

本项目使用 [Apache 2.0](LICENSE) 许可证。
