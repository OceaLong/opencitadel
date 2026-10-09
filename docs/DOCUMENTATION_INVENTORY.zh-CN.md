[English](DOCUMENTATION_INVENTORY.md)

# 文档清单

本清单覆盖当前工作区 116 份维护 Markdown 文档（53 对双语与 10 份单语言）及 13 份历史 specs。维护文档按当前代码同步；历史归档只提供当时的背景，不能作为当前行为、版本或验收状态的依据。新增、移动或废弃文档时同步更新。

本清单排除依赖、缓存、嵌套 Worktree、临时产物和本地执行记录（`node_modules/`、`.venv/`、`.pytest_cache/`、`.worktrees/`、`tmp/`、`.superpowers/`、`docs/superpowers/`）。`specs/` 单独列为历史归档；不把它们提升为维护参考。

**图例**

| 列       | 含义                                                                                                                                                                                                |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 权威性   | `primary` = 维护参考（以实现代码为准）；`index` = 导航；`module` = 模块入口；`tutorial` = 操作教程；`governance` = 开源治理；`internal` = 运维/开发契约；`fixture` = 测试输入；`archive` = 历史归档 |
| 双语     | `paired` = 两个真实存在的语言文件；`single` = 仅一个文件（不虚称双语）                                                                                                                              |
| 图示     | `svg/png` = `docs/assets/diagrams/` 技术图引用；`mermaid` = 实际 Mermaid 块；`none` = 无技术图。README 演示截图不计入                                                                               |
| 过期风险 | `low` / `medium` / `high`；归档与尚未完成 AC21 的容量文档保留 `high`                                                                                                                                |

## 根目录与文档中心

| 路径                                                                | 主题                         | 权威性     | 双语   | 图示    | 代码锚点                | 过期风险 |
| ------------------------------------------------------------------- | ---------------------------- | ---------- | ------ | ------- | ----------------------- | -------- |
| [README.md](../README.zh-CN.md)                                     | 项目概览、快速开始、文档地图 | index      | paired | svg/png | —                       | medium   |
| [docs/README.md](README.zh-CN.md)                                   | 文档导航中枢                 | index      | paired | none    | —                       | low      |
| [docs/MAINTENANCE_CHECKLIST.md](MAINTENANCE_CHECKLIST.zh-CN.md)     | PR 清单、同步规则            | governance | paired | none    | `scripts/check-docs.sh` | low      |
| [docs/DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.zh-CN.md) | 本清单                       | governance | paired | none    | `scripts/check-docs.sh` | low      |

## 架构（`docs/architecture/`）

| 路径                                                                                                      | 主题                                                  | 权威性  | 双语   | 图示    | 代码锚点                                                                                                                                                                    | 过期风险 |
| --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------- | ------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| [docs/architecture/overview.md](architecture/overview.zh-CN.md)                                           | 系统设计、强类型装配、API/Kernel、沙箱                | primary | paired | svg/png | `api/app/composition/`, `api/app/execution_kernel_main.py`                                                                                                                  | low      |
| [docs/architecture/governance-plane.md](architecture/governance-plane.zh-CN.md)                           | 效果契约、能力收窄、审批、终态闩、证据                | primary | paired | svg/png | `api/app/domain/models/tool_policy.py`, `api/app/application/execution/`                                                                                                    | medium   |
| [docs/architecture/security-model.md](architecture/security-model.zh-CN.md)                               | 信任边界、认证、密钥                                  | primary | paired | svg/png | `api/app/infrastructure/security/`, `api/core/config.py`                                                                                                                    | medium   |
| [docs/architecture/execution-kernel.md](architecture/execution-kernel.zh-CN.md)                           | Command、Event、Activity、投影、SSE 与恢复            | primary | paired | svg/png | `api/app/domain/execution/`, `api/app/application/execution/`, `api/app/execution_kernel.py`                                                                                | low      |
| [docs/architecture/web-operator.md](architecture/web-operator.zh-CN.md)                                   | 精确主机边界、审批、证据                              | primary | paired | none    | `api/app/application/execution/agent_tool_catalog.py`, `api/app/domain/services/tools/`                                                                                     | low      |
| [docs/architecture/teams-and-workspaces.md](architecture/teams-and-workspaces.zh-CN.md)                   | 团队、`X-Workspace-Id`                                | primary | paired | svg/png | `api/app/interfaces/endpoints/team_routes.py`                                                                                                                               | low      |
| [docs/architecture/admin-auditor-compliance.md](architecture/admin-auditor-compliance.zh-CN.md)           | 管理、审计、合规                                      | primary | paired | none    | `api/app/interfaces/endpoints/admin_routes.py`, `api/app/interfaces/endpoints/compliance_routes.py`, `ui/src/app/admin/`                                                    | medium   |
| [docs/architecture/integrations-a2a-service-keys.md](architecture/integrations-a2a-service-keys.zh-CN.md) | A2A、服务 API Key                                     | primary | paired | none    | `api/app/interfaces/endpoints/integration_routes.py`, `api/app/interfaces/endpoints/service_api_key_routes.py`                                                              | low      |
| [docs/architecture/skills.md](architecture/skills.zh-CN.md)                                               | Skill 模板、运行时                                    | primary | paired | none    | `api/app/application/services/skill_service.py`, `api/app/application/execution/agent_tool_catalog.py`                                                                      | low      |
| [docs/architecture/artifacts-sharing.md](architecture/artifacts-sharing.zh-CN.md)                         | 交付物、公开分享                                      | primary | paired | svg/png | `api/app/interfaces/endpoints/artifact_routes.py`                                                                                                                           | low      |
| [docs/architecture/automation-scheduler.md](architecture/automation-scheduler.zh-CN.md)                   | Cron、Webhook、Leader 选举                            | primary | paired | svg/png | `api/app/interfaces/endpoints/scheduling_routes.py`, `api/app/execution_kernel.py`                                                                                          | low      |
| [docs/architecture/ops-patrol.md](architecture/ops-patrol.zh-CN.md)                                       | Pack/Run 生命周期、Collector 边界与证据               | primary | paired | svg/png | `api/app/interfaces/endpoints/patrol_routes.py`, `api/app/application/services/patrol_run_service.py`                                                                       | low      |
| [docs/architecture/config-source-governance.md](architecture/config-source-governance.zh-CN.md)           | 部署、Policy、Integration 权威边界                    | primary | paired | none    | `api/core/config.py`, `api/app/application/services/runtime_policy_service.py`                                                                                              | medium   |
| [docs/architecture/runtime-policy-control-plane.md](architecture/runtime-policy-control-plane.zh-CN.md)   | Runtime Policy Revision、Head、Reader、Consumer Model | primary | paired | svg/png | `api/app/application/services/runtime_policy_service.py`, `api/app/application/services/runtime_policy_reader.py`                                                           | medium   |
| [docs/architecture/model-resilience.md](architecture/model-resilience.zh-CN.md)                           | 熔断、回退                                            | primary | paired | none    | `api/app/infrastructure/external/llm/resilient_llm.py`                                                                                                                      | low      |
| [docs/architecture/knowledge-base-ingestion.md](architecture/knowledge-base-ingestion.zh-CN.md)           | KB 解析、OCR、GraphRAG、摄取失败                      | primary | paired | svg/png | `api/app/application/services/knowledge_base_service.py`, `api/app/domain/services/knowledge_base/`                                                                         | medium   |
| [docs/architecture/architecture-evolution.md](architecture/architecture-evolution.zh-CN.md)               | Compose → K8s 演进                                    | primary | paired | none    | `docker-compose.yml`, `deploy/helm/`, `deploy/kustomize/`                                                                                                                   | low      |
| [docs/architecture/inference-control-plane.md](architecture/inference-control-plane.zh-CN.md)             | 推理 Endpoint/Model/Binding 控制面                    | primary | paired | none    | `api/app/interfaces/endpoints/inference_routes.py`, `ui/src/components/settings/inference-settings.tsx`                                                                     | low      |
| [docs/architecture/frontend-ui.md](architecture/frontend-ui.zh-CN.md)                                     | Next.js 前端架构                                      | primary | paired | svg/png | `ui/src/`                                                                                                                                                                   | low      |
| [docs/architecture/execution-analysis.md](architecture/execution-analysis.zh-CN.md)                       | 执行观察切面、分析源、比较、导出与当前授权            | primary | paired | svg/png | `api/app/application/services/execution_analysis_service.py`, `api/app/infrastructure/repositories/db_execution_comparison_repository.py`, `api/app/application/execution/` | low      |
| [docs/architecture/evaluation-control-plane.md](architecture/evaluation-control-plane.zh-CN.md)           | 评测 Batch、Subject/Judge、环境租约与物理调用预算     | primary | paired | svg/png | `api/app/composition/evaluation.py`, `api/app/application/evaluation/`, `api/app/infrastructure/execution/`                                                                 | low      |
| [docs/architecture/technical-decisions.md](architecture/technical-decisions.zh-CN.md)                     | 技术选型与对比                                        | primary | paired | none    | `api/pyproject.toml`, `ui/package.json`, `docker-compose.yml`                                                                                                               | low      |

## 运维与教程

| 路径                                                                                                            | 主题                          | 权威性   | 双语   | 图示 | 代码锚点                                                                      | 过期风险 |
| --------------------------------------------------------------------------------------------------------------- | ----------------------------- | -------- | ------ | ---- | ----------------------------------------------------------------------------- | -------- |
| [docs/operations/deployment.md](operations/deployment.zh-CN.md)                                                 | 生产部署、探针、有界排空      | primary  | paired | none | `docker-compose.yml`, `deploy/helm/opencitadel/`, `scripts/backup_tool.py`    | low      |
| [docs/operations/ops-patrol.md](operations/ops-patrol.zh-CN.md)                                                 | Patrol 启用、部署、证据与恢复 | primary  | paired | none | `ops-collector/`, `ops-actuator/`, `deploy/helm/`                             | low      |
| [docs/operations/https-domain-setup.md](operations/https-domain-setup.zh-CN.md)                                 | HTTPS 与域名                  | primary  | paired | none | `nginx/nginx.conf`, `deploy/helm/opencitadel/`                                | low      |
| [docs/tutorials/01-self-host-10-minutes.md](tutorials/01-self-host-10-minutes.zh-CN.md)                         | 10 分钟自托管                 | tutorial | paired | none | `scripts/quickstart.sh`                                                       | low      |
| [docs/tutorials/02-internal-knowledge-base.md](tutorials/02-internal-knowledge-base.zh-CN.md)                   | 知识库 RAG                    | tutorial | paired | none | `api/app/interfaces/endpoints/knowledge_base_routes.py`                       | low      |
| [docs/tutorials/03-mcp-integrations.md](tutorials/03-mcp-integrations.zh-CN.md)                                 | MCP 集成                      | tutorial | paired | none | `api/app/interfaces/endpoints/integration_routes.py`                          | low      |
| [docs/tutorials/04-governed-web-operator.md](tutorials/04-governed-web-operator.zh-CN.md)                       | Web Operator 教程             | tutorial | paired | none | `scripts/quickstart.sh`, `api/app/domain/models/tool_policy.py`               | low      |
| [docs/tutorials/05-refund-reconciliation-compliance.md](tutorials/05-refund-reconciliation-compliance.zh-CN.md) | 合规演示                      | tutorial | paired | none | `demo/ops-console/`, `api/app/interfaces/endpoints/compliance_routes.py`      | low      |
| [docs/tutorials/06-ops-patrol.md](tutorials/06-ops-patrol.zh-CN.md)                                             | Kubernetes 只读巡检教程       | tutorial | paired | none | `ui/src/app/patrols/`, `ops-collector/`                                       | low      |
| [docs/tutorials/07-approved-remediation.md](tutorials/07-approved-remediation.zh-CN.md)                         | 已批准的 Ops Patrol 修复教程  | tutorial | paired | none | `api/app/application/services/patrol_remediation_service.py`, `ops-actuator/` | low      |
| [docs/tutorials/08-ten-minute-governance-demo.md](tutorials/08-ten-minute-governance-demo.zh-CN.md)             | 纯 Compose 端到端治理演示闭环 | tutorial | paired | none | `scripts/quickstart.sh`, `api/app/seed_demo.py`                               | low      |

## 模块 README

| 路径                                                                            | 主题                              | 权威性 | 双语   | 图示    | 代码锚点                                                                                                | 过期风险 |
| ------------------------------------------------------------------------------- | --------------------------------- | ------ | ------ | ------- | ------------------------------------------------------------------------------------------------------- | -------- |
| [api/README.md](../api/README.zh-CN.md)                                         | 后端路由、SSE、开发               | module | paired | svg/png | `api/app/interfaces/endpoints/`, `api/app/composition/`                                                 | low      |
| [ui/README.md](../ui/README.zh-CN.md)                                           | 前端栈、路由                      | module | paired | svg/png | `ui/src/app/`                                                                                           | low      |
| [sandbox/README.md](../sandbox/README.zh-CN.md)                                 | 沙箱服务                          | module | paired | none    | `sandbox/`                                                                                              | low      |
| [nginx/README.md](../nginx/README.zh-CN.md)                                     | 网关、SSE/WS、上传限制            | module | paired | svg/png | `nginx/nginx.conf`                                                                                      | low      |
| [ops-collector/README.md](../ops-collector/README.zh-CN.md)                     | 固定只读探针与配置                | module | paired | none    | `ops-collector/src/`                                                                                    | low      |
| [ops-actuator/README.md](../ops-actuator/README.zh-CN.md)                       | 固定仅 patch 的写探针与配置       | module | paired | svg/png | `ops-actuator/src/`                                                                                     | low      |
| [deploy/helm/opencitadel/README.md](../deploy/helm/opencitadel/README.zh-CN.md) | Helm 安装                         | module | paired | none    | `deploy/helm/opencitadel/`                                                                              | low      |
| [deploy/patrol-demo/README.md](../deploy/patrol-demo/README.zh-CN.md)           | 一次性 Patrol 故障实验室          | module | paired | none    | `scripts/run-patrol-fixtures.sh`                                                                        | low      |
| [demo/ops-console/README.md](../demo/ops-console/README.zh-CN.md)               | Web Operator 演示后端             | module | paired | none    | `demo/ops-console/`                                                                                     | low      |
| [e2e/README.md](../e2e/README.zh-CN.md)                                         | 确定性全栈验收、证据与清理        | module | paired | none    | `e2e/playwright.config.ts`, `scripts/acceptance/runner.py`, `contracts/acceptance-evidence.schema.json` | high     |
| [scripts/README.md](../scripts/README.zh-CN.md)                                 | quickstart、文档检查、验收 Runner | module | paired | none    | `scripts/`                                                                                              | medium   |
| [deploy/scripts/README.md](../deploy/scripts/README.zh-CN.md)                   | 主机调优脚本                      | module | paired | none    | `deploy/scripts/`                                                                                       | low      |

## 开源治理（`.github/`）

| 路径                                                                          | 主题     | 权威性     | 双语   | 图示 | 代码锚点 | 过期风险 |
| ----------------------------------------------------------------------------- | -------- | ---------- | ------ | ---- | -------- | -------- |
| [.github/CONTRIBUTING.md](../.github/CONTRIBUTING.zh-CN.md)                   | 贡献指南 | governance | paired | none | —        | low      |
| [.github/SECURITY.md](../.github/SECURITY.zh-CN.md)                           | 漏洞披露 | governance | paired | none | —        | low      |
| [.github/CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.zh-CN.md)             | 行为准则 | governance | paired | none | —        | low      |
| [.github/pull_request_template.md](../.github/pull_request_template.zh-CN.md) | PR 模板  | governance | paired | none | —        | low      |

## 评测、容量、Schema 与 Fixture 参考

| 路径                                                                                              | 主题                                               | 权威性   | 双语   | 图示 | 代码锚点                                                                                                                      | 过期风险 |
| ------------------------------------------------------------------------------------------------- | -------------------------------------------------- | -------- | ------ | ---- | ----------------------------------------------------------------------------------------------------------------------------- | -------- |
| [docs/evaluation-environments.md](evaluation-environments.md)                                     | 受控环境清单、注册、租约、清理与修复               | primary  | single | none | `api/app/interfaces/endpoints/evaluation_environment_routes.py`, `api/app/infrastructure/evaluation/environment_inventory.py` | low      |
| [deploy/evaluation/budget-inventory.md](../deploy/evaluation/budget-inventory.md)                 | 原生与 Fixture 预算 Profile 及发布证明             | internal | single | none | `api/app/domain/evaluation/budget_capabilities.py`, `api/app/application/evaluation/scheduler.py`                             | low      |
| [deploy/evaluation/environment-capacity.md](../deploy/evaluation/environment-capacity.md)         | 环境占用与不可变运维策略激活                       | internal | single | none | `api/app/composition/environment_capacity.py`, `api/scripts/environment_capacity_policy.py`                                   | low      |
| [deploy/evaluation/execution-policy.md](../deploy/evaluation/execution-policy.md)                 | Subject/Judge 执行槽与策略激活                     | internal | single | none | `api/app/composition/evaluation_execution.py`, `api/scripts/evaluation_execution_policy.py`                                   | low      |
| [deploy/evaluation/physical-policy.md](../deploy/evaluation/physical-policy.md)                   | 持久 Provider 占用、请求者证明与 Judge 修复轮次    | internal | single | none | `api/app/composition/physical_budget.py`, `api/scripts/physical_budget_policy.py`                                             | low      |
| [scripts/execution_capacity/README.md](../scripts/execution_capacity/README.md)                   | 历史/探针/批次语料工具与未完成的 AC21 门禁         | internal | single | none | `scripts/seed_execution_visualization.py`, `scripts/execution_capacity/child.py`                                              | high     |
| [scripts/execution_capacity/LIVE.md](../scripts/execution_capacity/LIVE.md)                       | 有限来源负载、进度身份与负载窗口契约               | internal | single | none | `scripts/execution_capacity/live.py`, `scripts/execution_capacity/live_main.py`                                               | high     |
| [scripts/execution_capacity/REFERENCE.md](../scripts/execution_capacity/REFERENCE.md)             | 参考机物理轮次、原始重放与未完成的 Native/复用集成 | internal | single | none | `scripts/execution_capacity/reference_round.py`, `scripts/execution_capacity/offline_context.py`                              | high     |
| [api/app/domain/execution/EVOLUTION.md](../api/app/domain/execution/EVOLUTION.md)                 | 事件/命令演进规则与快照序列化守卫                  | internal | single | none | `api/app/domain/execution/run.py`, `api/tests/app/domain/execution/test_schema_guards.py`                                     | low      |
| [e2e/fixtures/knowledge/acceptance-handbook.md](../e2e/fixtures/knowledge/acceptance-handbook.md) | 规范可检索事实与降级断言测试输入                   | fixture  | single | none | `e2e/resources.spec.ts`                                                                                                       | low      |

## 历史归档（`specs/`；13 份）

下列文件保留原始内容与当时的版本/结果；它们不约束当前实现，也不证明当前验收通过。当前契约应查看上面的维护参考及其代码锚点。

| 路径                                                                                              | 主题                           | 权威性  | 双语   | 图示 | 代码锚点 | 过期风险 |
| ------------------------------------------------------------------------------------------------- | ------------------------------ | ------- | ------ | ---- | -------- | -------- |
| [specs/2026-09-03-execution-report.md](../specs/2026-09-03-execution-report.md)                   | 2026-09-03 优化计划执行报告    | archive | single | none | —        | high     |
| [specs/2026-09-03-kernel-architecture-audit.md](../specs/2026-09-03-kernel-architecture-audit.md) | 2026-09-03 执行内核架构审计    | archive | single | none | —        | high     |
| [specs/2026-09-03-kernel-overhaul-spec.md](../specs/2026-09-03-kernel-overhaul-spec.md)           | 2026-09-03 执行内核改造提案    | archive | single | none | —        | high     |
| [specs/2026-09-03-optimization-overview.md](../specs/2026-09-03-optimization-overview.md)         | 历史功能闭环优化提案           | archive | single | none | —        | high     |
| [specs/2026-09-04-kernel-overhaul-report.md](../specs/2026-09-04-kernel-overhaul-report.md)       | 2026-09-04 执行内核改造报告    | archive | single | none | —        | high     |
| [specs/plan-a-p0-fixes.md](../specs/plan-a-p0-fixes.md)                                           | Plan A：历史 P0 修复计划       | archive | single | none | —        | high     |
| [specs/plan-b-backend-closure.md](../specs/plan-b-backend-closure.md)                             | Plan B：历史后端闭环计划       | archive | single | none | —        | high     |
| [specs/plan-c-frontend-closure.md](../specs/plan-c-frontend-closure.md)                           | Plan C：历史前端闭环计划       | archive | single | none | —        | high     |
| [specs/plan-d-deploy-observability.md](../specs/plan-d-deploy-observability.md)                   | Plan D：历史部署与可观测性计划 | archive | single | none | —        | high     |
| [specs/plan-k1-domain-events.md](../specs/plan-k1-domain-events.md)                               | Plan K1：历史领域/事件改造计划 | archive | single | none | —        | high     |
| [specs/plan-k2-runtime-loops.md](../specs/plan-k2-runtime-loops.md)                               | Plan K2：历史运行时循环计划    | archive | single | none | —        | high     |
| [specs/plan-k3-plugin-surface.md](../specs/plan-k3-plugin-surface.md)                             | Plan K3：历史扩展面计划        | archive | single | none | —        | high     |
| [specs/plan-k4-projection-observability.md](../specs/plan-k4-projection-observability.md)         | Plan K4：历史投影/观测计划     | archive | single | none | —        | high     |

## 维护

- 文档 PR 前运行 `./scripts/check-docs.sh`。
- 路由、配置、UI 或执行契约变化时，同步文档与代码锚点；图示列按实际内容刷新。
- 新架构主题添加中英文，在 [docs/README.md](README.zh-CN.md) 建链并更新本清单。内部单语言契约可以保持 `single`。
- Fixture 文字可能是测试断言；修改前核对测试契约。历史 specs 保持归档分类。
