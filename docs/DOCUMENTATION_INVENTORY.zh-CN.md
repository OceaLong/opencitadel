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
| [overview.md](architecture/overview.zh-CN.md) | 系统设计、强类型装配、API/Kernel、沙箱 | primary | paired | mermaid | `api/app/composition/`、`api/app/execution_kernel.py` | low |
| [governance-plane.md](architecture/governance-plane.zh-CN.md) | 效果契约、能力收窄、审批、终态闩、证据 | primary | paired | mermaid | `tool_policy.py`、`application/execution/`、`governance_profile_service.py`、`evidence_service.py` | medium |
| [security-model.md](architecture/security-model.zh-CN.md) | 信任边界、认证、密钥 | primary | paired | mermaid | `infrastructure/security/` | medium |
| [execution-kernel.md](architecture/execution-kernel.zh-CN.md) | Command、Event、Activity、投影、SSE 与恢复 | primary | paired | mermaid | `domain/execution/`、`application/execution/`、`execution_kernel.py` | low |
| [web-operator.md](architecture/web-operator.zh-CN.md) | 精确主机边界、审批、证据 | primary | paired | mermaid | `application/execution/agent_tool_catalog.py`、`tools/browser.py` | low |
| [teams-and-workspaces.md](architecture/teams-and-workspaces.zh-CN.md) | 团队、`X-Workspace-Id` | primary | paired | mermaid | `team_routes.py` | low |
| [admin-auditor-compliance.md](architecture/admin-auditor-compliance.zh-CN.md) | 管理、审计、合规 | primary | paired | mermaid | `admin_routes.py` | medium |
| [integrations-a2a-service-keys.md](architecture/integrations-a2a-service-keys.zh-CN.md) | A2A、服务 API Key | primary | paired | mermaid | `a2a_routes.py` | low |
| [skills.md](architecture/skills.zh-CN.md) | Skill 模板、运行时 | primary | paired | mermaid | `skill_service.py`、`application/execution/agent_tool_catalog.py` | low |
| [artifacts-sharing.md](architecture/artifacts-sharing.zh-CN.md) | 交付物、公开分享 | primary | paired | mermaid | `artifact_routes.py` | low |
| [automation-scheduler.md](architecture/automation-scheduler.zh-CN.md) | Cron、Webhook、Leader 选举 | primary | paired | mermaid | `scheduling_routes.py` | low |
| [ops-patrol.md](architecture/ops-patrol.zh-CN.md) | Pack/Run 生命周期、Collector 边界与证据 | primary | paired | mermaid | `patrol_routes.py`、`patrol_run_service.py` | low |
| [config-source-governance.md](architecture/config-source-governance.zh-CN.md) | 部署、Policy、Integration 权威边界 | primary | paired | none | `core/config.py`、`runtime_policy_service.py` | medium |
| [runtime-policy-control-plane.md](architecture/runtime-policy-control-plane.zh-CN.md) | Runtime Policy Revision、Head、Reader、Consumer Model | primary | paired | mermaid | `runtime_policy_service.py`、`runtime_policy_reader.py` | medium |
| [model-resilience.md](architecture/model-resilience.zh-CN.md) | 熔断、回退 | primary | paired | mermaid | `resilient_llm.py` | low |
| [codebase-reindex.md](architecture/codebase-reindex.zh-CN.md) | Codebase 摄取、向量恢复 | primary | paired | mermaid | `codebase/ingestion_runner.py` | medium |
| [knowledge-base-ingestion.md](architecture/knowledge-base-ingestion.zh-CN.md) | KB 解析、OCR、GraphRAG、摄取失败 | primary | paired | mermaid | `knowledge_base/ingestion_runner.py` | medium |
| [architecture-evolution.md](architecture/architecture-evolution.zh-CN.md) | Compose → K8s 演进 | primary | paired | mermaid | `deploy/helm/` | low |
| [inference-control-plane.md](architecture/inference-control-plane.zh-CN.md) | 推理 Endpoint/Model/Binding 控制面 | primary | paired | mermaid | `inference_routes.py`、`inference-settings.tsx` | low |
| [frontend-ui.md](architecture/frontend-ui.zh-CN.md) | Next.js 前端架构 | primary | paired | mermaid | `ui/src/` | low |
| [technical-decisions.md](architecture/technical-decisions.zh-CN.md) | 技术选型与对比 | primary | paired | mermaid | — | low |

## 运维与教程

| 路径 | 主题 | 权威性 | 双语 | 图示 | 代码锚点 | 过期风险 |
|------|------|--------|------|------|----------|----------|
| [operations/deployment.md](operations/deployment.zh-CN.md) | 生产部署、探针、有界排空 | primary | paired | mermaid | `docker-compose.yml`、`deploy/helm/opencitadel/` | low |
| [operations/ops-patrol.md](operations/ops-patrol.zh-CN.md) | Patrol 启用、部署、证据与恢复 | primary | paired | mermaid | `ops-collector/`、`ops-actuator/`、`deploy/helm/` | low |
| [operations/https-domain-setup.md](operations/https-domain-setup.zh-CN.md) | HTTPS 与域名 | primary | paired | none | `.env.example` | low |
| [tutorials/01-self-host-10-minutes.md](tutorials/01-self-host-10-minutes.zh-CN.md) | 10 分钟自托管 | tutorial | paired | none | `scripts/quickstart.sh` | low |
| [tutorials/02-internal-knowledge-base.md](tutorials/02-internal-knowledge-base.zh-CN.md) | 知识库 RAG | tutorial | paired | mermaid | `knowledge-base-ingestion.md` | low |
| [tutorials/03-mcp-integrations.md](tutorials/03-mcp-integrations.zh-CN.md) | MCP 集成 | tutorial | paired | none | `integration_routes.py` | low |
| [tutorials/04-governed-web-operator.md](tutorials/04-governed-web-operator.zh-CN.md) | Web Operator 教程 | tutorial | paired | none | `operator-scope-dialog.tsx` | low |
| [tutorials/05-refund-reconciliation-compliance.md](tutorials/05-refund-reconciliation-compliance.zh-CN.md) | 合规演示 | tutorial | paired | none | `compliance_routes.py` | low |
| [tutorials/06-ops-patrol.md](tutorials/06-ops-patrol.zh-CN.md) | Kubernetes 只读巡检教程 | tutorial | paired | none | `ui/src/app/patrols/` | low |
| [tutorials/07-approved-remediation.md](tutorials/07-approved-remediation.zh-CN.md) | 已批准的 Ops Patrol 修复教程 | tutorial | paired | none | `ops-actuator/` | low |
| [tutorials/08-ten-minute-governance-demo.md](tutorials/08-ten-minute-governance-demo.zh-CN.md) | 纯 Compose 端到端治理演示闭环 | tutorial | paired | none | `scripts/quickstart.sh`、`app/seed_demo.py` | low |

## 模块 README

| 路径 | 主题 | 权威性 | 双语 | 图示 | 过期风险 |
|------|------|--------|------|------|----------|
| [api/README.md](../api/README.zh-CN.md) | 后端路由、SSE、开发 | module | paired | none | low |
| [ui/README.md](../ui/README.zh-CN.md) | 前端栈、路由 | module | paired | none | low |
| [sandbox/README.md](../sandbox/README.zh-CN.md) | 沙箱服务 | module | paired | none | low |
| [nginx/README.md](../nginx/README.zh-CN.md) | 网关、SSE/WS、上传限制 | module | paired | mermaid | low |
| [ops-collector/README.md](../ops-collector/README.zh-CN.md) | 固定只读探针与配置 | module | paired | none | low |
| [ops-actuator/README.md](../ops-actuator/README.zh-CN.md) | 固定仅 patch 的写探针与配置 | module | paired | none | low |
| [deploy/helm/opencitadel/README.md](../deploy/helm/opencitadel/README.zh-CN.md) | Helm 安装 | module | paired | none | low |
| [deploy/patrol-demo/README.md](../deploy/patrol-demo/README.zh-CN.md) | 一次性 Patrol 故障实验室 | module | paired | none | low |
| [demo/ops-console/README.md](../demo/ops-console/README.zh-CN.md) | Web Operator 演示后端 | module | paired | none | low |
| [e2e/README.md](../e2e/README.zh-CN.md) | 确定性全栈验收、证据与清理 | module | paired | none | high |
| [scripts/README.md](../scripts/README.zh-CN.md) | quickstart、文档检查、验收 Runner | module | paired | none | medium |
| [deploy/scripts/README.md](../deploy/scripts/README.zh-CN.md) | 主机调优脚本 | module | paired | none | low |

## 开源治理（`.github/`）

| 路径 | 主题 | 权威性 | 双语 | 过期风险 |
|------|------|--------|------|----------|
| [CONTRIBUTING.md](../.github/CONTRIBUTING.zh-CN.md) | 贡献指南 | governance | paired | low |
| [SECURITY.md](../.github/SECURITY.zh-CN.md) | 漏洞披露 | governance | paired | low |
| [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.zh-CN.md) | 行为准则 | governance | paired | low |
| [pull_request_template.md](../.github/pull_request_template.zh-CN.md) | PR 模板 | governance | paired | low |

## 维护

- 文档 PR 前运行 `./scripts/check-docs.sh`。
- 代码变更路由、配置或 UI 流程时，同步更新对应文档并将过期风险改回 `low`。
- 新架构主题：添加中英文、在 [docs/README.md](README.zh-CN.md) 建链、更新本清单。
