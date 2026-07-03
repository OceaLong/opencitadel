# OpenCitadel 文档中心

[English](README.md)

本文档是 OpenCitadel 文档的导航入口。每份文档采用 **成对维护**：`*.md`（英文）与 `*.zh-CN.md`（中文）。

## 推荐阅读路径

### 首次体验

1. [10 分钟自托管](tutorials/01-self-host-10-minutes.zh-CN.md)
2. [内部知识库教程](tutorials/02-internal-knowledge-base.zh-CN.md)
3. [MCP 集成教程](tutorials/03-mcp-integrations.zh-CN.md)
4. [受治理 Web Operator](tutorials/04-governed-web-operator.zh-CN.md)

### 运维与部署

| 文档 | 权威范围 |
|------|----------|
| [README.zh-CN.md](../README.zh-CN.md) | 项目概览与文档地图 |
| [生产部署](operations/deployment.zh-CN.md) | Docker Compose 生产部署、cloud/local 模式、备份与调优 |
| [域名与 HTTPS](operations/https-domain-setup.zh-CN.md) | 域名绑定与 HTTPS |
| [Helm Chart](../deploy/helm/opencitadel/README.zh-CN.md) | Kubernetes / Helm 安装与 Values |

### 架构与设计

| 文档 | 权威范围 |
|------|----------|
| [系统架构](architecture/overview.zh-CN.md) | 总体架构、进程职责、沙箱生命周期、部署形态 |
| [安全模型](architecture/security-model.zh-CN.md) | 信任边界、沙箱隔离、认证与授权 |
| [事件系统](architecture/events.zh-CN.md) | 领域事件、SSE 契约、持久化与 replay |
| [检查点与 HITL](architecture/checkpoints-and-hitl.zh-CN.md) | 门控契约、回滚、Web Operator、浏览器 Profile 快照 |
| [Web Operator](architecture/web-operator.zh-CN.md) | 门控档位、审计契约、OpsConsole 演示 |
| [自动化与调度](architecture/automation-scheduler.zh-CN.md) | Cron/Webhook 任务、Leader 选举、通知 |
| [应用市场](architecture/marketplace.zh-CN.md) | LLM 小应用目录与契约 |
| [配置来源治理](architecture/config-source-governance.zh-CN.md) | AppConfig、config.yaml、环境变量边界 |
| [模型韧性设计](architecture/model-resilience.zh-CN.md) | 熔断、fallback、SLO 与运行手册 |
| [API/SSE 协议兼容策略](architecture/contract-compatibility.zh-CN.md) | 前后端契约兼容窗口 |
| [Codebase 向量降级与重新索引](architecture/codebase-reindex.zh-CN.md) | embedding 不可用时的降级与恢复 |
| [架构演进指南](architecture/architecture-evolution.zh-CN.md) | Compose → K8s / 外置沙箱演进 |

### 模块说明

| 文档 | 范围 |
|------|------|
| [API](../api/README.zh-CN.md) | 后端路由、SSE、本地开发 |
| [UI](../ui/README.zh-CN.md) | 前端技术栈与路由 |
| [Sandbox](../sandbox/README.zh-CN.md) | 隔离运行时 |

### 开源治理

| 文档 | 说明 |
|------|------|
| [CONTRIBUTING.zh-CN.md](../.github/CONTRIBUTING.zh-CN.md) | 贡献指南 |
| [SECURITY.zh-CN.md](../.github/SECURITY.zh-CN.md) | 漏洞披露政策 |
| [CODE_OF_CONDUCT.zh-CN.md](../.github/CODE_OF_CONDUCT.zh-CN.md) | 行为准则 |

## 维护规则

- **一主题一权威文档**：避免在 README 与专题文档中重复维护同一策略。
- **配置事实来源**：环境变量以 `.env.example` 为准；行为配置以 `api/config.yaml` 为准。
- **双语成对**：新增或修改文档时，同步更新对应语言的配对文件。
- **链接约定**：中文文档优先链接 `*.zh-CN.md`；英文文档优先链接 `*.md`。
