# OpenCitadel 文档中心

[English](README.md)

本文档是 OpenCitadel 文档的导航入口。每份文档采用 **成对维护**：`*.md`（英文）与 `*.zh-CN.md`（中文）。

## 推荐阅读路径

### 首次体验

1. [10 分钟自托管](tutorials/01-self-host-10-minutes.zh-CN.md)
2. [内部知识库教程](tutorials/02-internal-knowledge-base.zh-CN.md)
3. [MCP 集成教程](tutorials/03-mcp-integrations.zh-CN.md)
4. [受治理 Web Operator](tutorials/04-governed-web-operator.zh-CN.md)
5. [退款对账与合规审计](tutorials/05-refund-reconciliation-compliance.zh-CN.md)
6. [只读每日 Ops Patrol](tutorials/06-ops-patrol.zh-CN.md)
7. [审批通过后执行 Ops Patrol 修复](tutorials/07-approved-remediation.zh-CN.md)
8. [10 分钟治理演示闭环](tutorials/08-ten-minute-governance-demo.zh-CN.md)

### 运维与部署

| 文档 | 权威范围 |
|------|----------|
| [README.zh-CN.md](../README.zh-CN.md) | 项目概览与文档地图 |
| [生产部署](operations/deployment.zh-CN.md) | Docker Compose 生产部署、cloud/local 模式、备份与调优 |
| [Ops Patrol 运维](operations/ops-patrol.zh-CN.md) | Collector 安全边界、部署、恢复、证据与排障 |
| [域名与 HTTPS](operations/https-domain-setup.zh-CN.md) | 域名绑定与 HTTPS |
| [Helm Chart](../deploy/helm/opencitadel/README.zh-CN.md) | Kubernetes / Helm 安装与 Values |

### 架构与设计

| 文档 | 权威范围 |
|------|----------|
| [系统架构](architecture/overview.zh-CN.md) | 总体架构、进程职责、沙箱生命周期、部署形态 |
| [治理平面](architecture/governance-plane.zh-CN.md) | 效果契约、能力收窄、整批审批、终态闩、证据 |
| [Ops Patrol](architecture/ops-patrol.zh-CN.md) | Pack/Run 生命周期、Collector 信任边界、证据与租户隔离 |
| [技术选型](architecture/technical-decisions.zh-CN.md) | 技术选择与替代方案对比 |
| [推理控制面](architecture/inference-control-plane.zh-CN.md) | Endpoint/Model/Binding 所有权、能力、加密与 UI 流程 |
| [前端 UI](architecture/frontend-ui.zh-CN.md) | Next.js Shell、公开 SSE 投影、审批界面 |
| [执行内核](architecture/execution-kernel.zh-CN.md) | Command、Event Store、Activity、恢复、投影、SSE 与权限边界 |
| [执行内核切换证据](architecture/execution-kernel-cutover-evidence.zh-CN.md) | Greenfield Schema 边界与可复核验收结果 |
| [安全模型](architecture/security-model.zh-CN.md) | 信任边界、沙箱隔离、认证与授权 |
| [Web Operator](architecture/web-operator.zh-CN.md) | 精确主机边界、逐调用审批、证据 |
| [团队与工作区](architecture/teams-and-workspaces.zh-CN.md) | 团队角色、`X-Workspace-Id`、邀请 |
| [管理、审计与合规](architecture/admin-auditor-compliance.zh-CN.md) | 平台管理、证据链、合规报告 |
| [A2A 与服务 API Key](architecture/integrations-a2a-service-keys.zh-CN.md) | 入站/出站 A2A、`X-Api-Key` |
| [Skills](architecture/skills.zh-CN.md) | Skill 模板、运行时覆盖、MCP/A2A 过滤 |
| [交付物与分享](architecture/artifacts-sharing.zh-CN.md) | 会话交付物、公开分享链接 |
| [自动化与调度](architecture/automation-scheduler.zh-CN.md) | Cron/Webhook 任务、Leader 选举、通知 |
| [配置来源治理](architecture/config-source-governance.zh-CN.md) | Deployment Settings、Runtime Policy、Integration 边界 |
| [Runtime Policy 控制面](architecture/runtime-policy-control-plane.zh-CN.md) | 不可变 Revision、原子 Head、CAS 与 Fail-closed Consumer |
| [模型韧性设计](architecture/model-resilience.zh-CN.md) | 熔断、fallback、SLO 与运行手册 |
| [Codebase 向量降级与重新索引](architecture/codebase-reindex.zh-CN.md) | embedding 不可用时的降级与恢复 |
| [知识库摄取](architecture/knowledge-base-ingestion.zh-CN.md) | 解析、OCR、GraphRAG、摄取失败 |
| [架构演进指南](architecture/architecture-evolution.zh-CN.md) | Compose → K8s / 外置沙箱演进 |

### 模块说明

| 文档 | 范围 |
|------|------|
| [API](../api/README.zh-CN.md) | 后端路由、SSE、本地开发 |
| [UI](../ui/README.zh-CN.md) | 前端技术栈与路由 |
| [Sandbox](../sandbox/README.zh-CN.md) | 隔离运行时 |
| [Nginx 网关](../nginx/README.zh-CN.md) | 边缘代理、SSE/WS、上传限制 |
| [Ops Collector](../ops-collector/README.zh-CN.md) | 固定只读 MCP 探针、配置与部署 |
| [Ops Actuator](../ops-actuator/README.zh-CN.md) | 固定仅 patch 写 MCP 探针、配置与部署 |
| [OpsConsole 演示](../demo/ops-console/README.zh-CN.md) | Web Operator 工单后台演示 |
| [E2E 验收](../e2e/README.zh-CN.md) | 确定性隔离全栈门禁、证据与清理 |
| [仓库脚本](../scripts/README.zh-CN.md) | `quickstart.sh`、`check-docs.sh`、验收 Runner |
| [部署脚本](../deploy/scripts/README.zh-CN.md) | 生产主机调优工具 |

### 开源治理

| 文档 | 说明 |
|------|------|
| [CONTRIBUTING.zh-CN.md](../.github/CONTRIBUTING.zh-CN.md) | 贡献指南 |
| [SECURITY.zh-CN.md](../.github/SECURITY.zh-CN.md) | 漏洞披露政策 |
| [CODE_OF_CONDUCT.zh-CN.md](../.github/CODE_OF_CONDUCT.zh-CN.md) | 行为准则 |

## 维护规则

- **一主题一权威文档**：避免在 README 与专题文档中重复维护同一策略。
- **配置事实来源**：部署输入以 `.env.example` 为准；实时行为以 PostgreSQL Runtime Policy Revision 为准。
- **双语成对**：新增或修改文档时，同步更新对应语言的配对文件。
- **链接约定**：中文文档优先链接 `*.zh-CN.md`；英文文档优先链接 `*.md`。
- **索引同步**：新增教程或架构文档时，同步更新本索引、根目录 [README.md](../README.md) / [README.zh-CN.md](../README.zh-CN.md) 文档地图，并在中英文文件文首添加语言切换链接。
- **PR 清单** — [文档维护检查清单](MAINTENANCE_CHECKLIST.zh-CN.md)（可执行步骤）；[文档清单](DOCUMENTATION_INVENTORY.zh-CN.md)（实时权威列表）；提交文档变更前运行 `./scripts/check-docs.sh`。
