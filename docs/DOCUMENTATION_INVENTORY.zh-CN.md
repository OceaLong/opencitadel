[English](DOCUMENTATION_INVENTORY.md)

# 文档清单

OpenCitadel Markdown 文档的权威清单。新增、移动或废弃文档时请同步更新本文件。

**图例**

| 列 | 含义 |
|----|------|
| 权威性 | `primary` = 权威来源；`index` = 仅导航；`module` = 模块开发入口；`governance` = 开源治理 |
| 双语 | `paired` = `*.md` + `*.zh-CN.md`；`single` = 仅一种语言 |
| 图示 | `mermaid` / `none` |
| 过期风险 | `low` / `medium` / `high`（人工审查） |

## 根目录与文档中心

| 路径 | 主题 | 权威性 | 双语 | 图示 | 代码锚点 | 过期风险 |
|------|------|--------|------|------|----------|----------|
| [README.md](../README.zh-CN.md) | 项目概览、快速开始、文档地图 | index | paired | mermaid | — | medium |
| [docs/README.md](README.zh-CN.md) | 文档导航中枢 | index | paired | none | — | low |
| [docs/MAINTENANCE_CHECKLIST.md](MAINTENANCE_CHECKLIST.zh-CN.md) | PR 清单、同步规则 | governance | paired | none | `scripts/check-docs.sh` | low |
| [docs/DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.zh-CN.md) | 本清单 | governance | paired | none | — | low |

## 架构（`docs/architecture/`）

| 路径 | 主题 | 权威性 | 双语 | 图示 | 代码锚点 | 过期风险 |
|------|------|--------|------|------|----------|----------|
| [overview.md](architecture/overview.zh-CN.md) | 系统设计、API/Worker、DI、沙箱 | primary | paired | mermaid | `api/app/container.py` | medium |
| [security-model.md](architecture/security-model.zh-CN.md) | 信任边界、认证、密钥 | primary | paired | mermaid | `infrastructure/security/` | medium |
| [events.md](architecture/events.zh-CN.md) | 领域事件、SSE、回放 | primary | paired | mermaid | `domain/models/event.py` | medium |
| [checkpoints-and-hitl.md](architecture/checkpoints-and-hitl.zh-CN.md) | HITL 门控、检查点、Web Operator | primary | paired | mermaid | `checkpoint_service.py` | medium |
| [web-operator.md](architecture/web-operator.zh-CN.md) | 门控档位、审计契约 | primary | paired | mermaid | `domain/services/agents/` | low |
| [teams-and-workspaces.md](architecture/teams-and-workspaces.zh-CN.md) | 团队、`X-Workspace-Id` | primary | paired | none | `team_routes.py` | low |
| [admin-auditor-compliance.md](architecture/admin-auditor-compliance.zh-CN.md) | 管理、审计、合规 | primary | paired | none | `admin_routes.py` | medium |
| [integrations-a2a-service-keys.md](architecture/integrations-a2a-service-keys.zh-CN.md) | A2A、服务 API Key | primary | paired | none | `a2a_routes.py` | low |
| [artifacts-sharing.md](architecture/artifacts-sharing.zh-CN.md) | 交付物、公开分享 | primary | paired | none | `artifact_routes.py` | low |
| [automation-scheduler.md](architecture/automation-scheduler.zh-CN.md) | Cron、Webhook、Leader 选举 | primary | paired | mermaid | `scheduling_routes.py` | low |
| [marketplace.md](architecture/marketplace.zh-CN.md) | 应用市场 | primary | paired | mermaid | `marketplace_routes.py` | low |
| [config-source-governance.md](architecture/config-source-governance.zh-CN.md) | AppConfig 与 env 边界 | primary | paired | mermaid | `core/config.py` | medium |
| [model-resilience.md](architecture/model-resilience.zh-CN.md) | 熔断、回退 | primary | paired | mermaid | `resilient_llm.py` | low |
| [contract-compatibility.md](architecture/contract-compatibility.zh-CN.md) | API/SSE 兼容窗口 | primary | paired | none | `event_upgrader.py` | low |
| [codebase-reindex.md](architecture/codebase-reindex.zh-CN.md) | Codebase 向量恢复 | primary | paired | mermaid | `codebase_routes.py` | low |
| [architecture-evolution.md](architecture/architecture-evolution.zh-CN.md) | Compose → K8s 演进 | primary | paired | mermaid | `deploy/helm/` | low |
| [llm-endpoints-and-models.md](architecture/llm-endpoints-and-models.zh-CN.md) | LLM 端点/模型拆分 | primary | paired | mermaid | `llm_endpoint_routes.py` | low |
| [frontend-ui.md](architecture/frontend-ui.zh-CN.md) | Next.js 前端架构 | primary | paired | mermaid | `ui/src/` | low |
| [task-recovery.md](architecture/task-recovery.zh-CN.md) | 可恢复任务重试 | primary | paired | mermaid | `recoverable_task_retry.py` | low |
| [technical-decisions.md](architecture/technical-decisions.zh-CN.md) | 技术选型与对比 | primary | paired | none | — | low |

## 运维与教程

| 路径 | 主题 | 权威性 | 双语 | 图示 | 代码锚点 | 过期风险 |
|------|------|--------|------|------|----------|----------|
| [operations/deployment.md](operations/deployment.zh-CN.md) | 生产部署 | primary | paired | mermaid | `docker-compose.yml` | low |
| [operations/https-domain-setup.md](operations/https-domain-setup.zh-CN.md) | HTTPS 与域名 | primary | paired | none | `.env.example` | low |
| [tutorials/01-self-host-10-minutes.md](tutorials/01-self-host-10-minutes.zh-CN.md) | 10 分钟自托管 | tutorial | paired | none | UI Settings | medium |
| [tutorials/02-internal-knowledge-base.md](tutorials/02-internal-knowledge-base.zh-CN.md) | 知识库 RAG | tutorial | paired | mermaid | `knowledge_base_routes.py` | medium |
| [tutorials/03-mcp-integrations.md](tutorials/03-mcp-integrations.md) | MCP 集成 | tutorial | paired | none | `app_config_routes.py` | low |
| [tutorials/04-governed-web-operator.md](tutorials/04-governed-web-operator.zh-CN.md) | Web Operator 教程 | tutorial | paired | none | `operator-scope-dialog.tsx` | low |
| [tutorials/05-refund-reconciliation-compliance.md](tutorials/05-refund-reconciliation-compliance.zh-CN.md) | 合规演示 | tutorial | paired | none | `compliance_routes.py` | low |

## 模块 README

| 路径 | 主题 | 权威性 | 双语 | 图示 | 过期风险 |
|------|------|--------|------|------|----------|
| [api/README.md](../api/README.zh-CN.md) | 后端路由、SSE、开发 | module | paired | none | medium |
| [ui/README.md](../ui/README.zh-CN.md) | 前端栈、路由 | module | paired | none | medium |
| [sandbox/README.md](../sandbox/README.zh-CN.md) | 沙箱服务 | module | paired | none | low |
| [deploy/helm/opencitadel/README.md](../deploy/helm/opencitadel/README.zh-CN.md) | Helm 安装 | module | paired | none | low |
| [demo/ops-console/README.md](../demo/ops-console/README.zh-CN.md) | Web Operator 演示后端 | module | paired | none | low |

## 开源治理（`.github/`）

| 路径 | 主题 | 权威性 | 双语 | 过期风险 |
|------|------|--------|------|----------|
| [CONTRIBUTING.md](../.github/CONTRIBUTING.zh-CN.md) | 贡献指南 | governance | paired | low |
| [SECURITY.md](../.github/SECURITY.zh-CN.md) | 漏洞披露 | governance | paired | low |
| [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.zh-CN.md) | 行为准则 | governance | paired | low |
| [pull_request_template.md](../.github/pull_request_template.zh-CN.md) | PR 模板 | governance | paired | low |

## 废弃候选（未经确认勿整篇删除）

| 位置 | 问题 | 处理 |
|------|------|------|
| `ui/README.md` | “Settings 待接入语言切换” | 已删除 — 使用 Header `LanguageToggle` |
| `admin-auditor-compliance.md` | `/admin/usage` UI 路由 | 已删除 — 用量图表在 `/admin` 概览 |
| 教程 | “Knowledge 在侧边栏” | 已更新 — Header 工作区菜单 |
| 多文档 | 重复的仅模型配置步骤 | 去重 — 链接到 `deployment.md` |

## 维护

- 文档 PR 前运行 `./scripts/check-docs.sh`。
- 代码变更路由、配置或 UI 流程时，同步更新对应文档并将过期风险改回 `low`。
- 新架构主题：添加中英文、在 [docs/README.md](README.zh-CN.md) 建链、更新本清单。
