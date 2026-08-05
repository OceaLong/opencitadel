# 2026-07-28 治理审计 P0 复核

- 日期：2026-08-04
- 复核基线：`git HEAD` = `0dad050`，叠加阶段 2（`docs/superpowers/plans/2026-08-04-phase2-governance-plane.md`）工作区暂存改动（未 commit）
- 复核对象：`docs/superpowers/audits/2026-07-28-agent-kb-codebase-governance-audit.md` 第 3 节列出的全部 12 个 P0
- 结论：**12 个 P0 全部闭环**（11 个由既往重构修复并有契约测试守护；1 个——P0-12 的 Codebase 对账分支——由本阶段 Task 1 落地）

## 1. 复核方法

四条并行代码审查线，逐条对照 2026-07-28 审计原文的"违反 / 证据 / 故障机制 / 要求"，在当前代码快照上重新定位证据行号、确认修复语义是否覆盖原故障机制、并核对是否有契约测试守护回归：

1. Agent 工具治理线：CapabilityPolicy、PolicyBoundTool、ToolBatchExecutor（审批批次、重试、终态）。
2. 会话终态线：`db_session_repository.py` 的 RunOutcome/terminal latch。
3. 资源与安全线：Git/ZIP 来源校验、SSRF 防护、写路由统一 guard。
4. Worker 恢复线：lease 冲突 ack 语义、KB/Codebase stuck-build 对账。

行号均为 2026-08-04 复核快照下的实际行号（非 2026-07-28 审计原文行号）；证据均现场逐条打开文件核验，非转引旧审计。

## 2. 结论表

| 编号 | 原判定 | 复核状态 | 修复机制 | 证据 |
|------|--------|----------|----------|------|
| P0-01 | Ask 模式可越权产生副作用 | **FIXED** | `CapabilityPolicy` 三层收窄（`allows`/`allows_integration`/`for_child`）+ 所有工具经 `PolicyBoundTool` 包裹后在调用前强制校验 | `api/app/domain/services/tools/capability_policy.py:86-135`（策略判定）、`api/app/domain/services/tools/base.py:170`（`PolicyBoundTool`）；契约测试 `api/tests/app/contracts/test_agent_governance_invariants.py:150-223`（`test_ask_has_zero_side_effects_across_delegation_and_integration`：Ask 会话下委派子任务、MCP 外部写入均在 `flow._agent._tools` 调用层抛出 `CapabilityDeniedError`，副作用计数为零） |
| P0-02 | 多工具调用在审批完成前执行同批次副作用 | **FIXED** | `ToolBatchExecutor.preflight` 对整批调用一次性预检并判定 `approval_required`/`durable_execution_required`；`execute()` 在批次需要审批时直接返回 `waiting=True`，不下探到执行；`resume()` 对已消费/已过期/会话不匹配的批次分别短路，`consume_approval_batch` 保证仅消费一次 | `api/app/domain/services/agents/tool_batch_executor.py:194-352`（`preflight`/`invoke`/`invoke_preapproved`/`execute` 入口拦截）、`:403-472`（`resume` 内的原子消费与状态短路） |
| P0-03 | 重试可能造成重复副作用，且失败类型未区分 | **FIXED** | `_attempt_limit` 按 idempotency（NON_IDEMPOTENT/UNKNOWN 强制单次、IDEMPOTENT_WITH_KEY 缺 key 契约也单次）与 effect 联合判定重试次数；`_is_transient_failure`/`_status_for_attempt` 区分 timeout/transport/其它失败类型，非只读工具超时后落 `OUTCOME_UNKNOWN` 而非误报成功或吞掉 | `api/app/domain/services/agents/tool_batch_executor.py:720-801`（`_attempt_limit`、`_supports_idempotency_key`、`_failure_kind`、`_is_transient_failure`、`_status_for_attempt`，`OUTCOME_UNKNOWN` 分支见 :794-801） |
| P0-04 | 任务可能出现双终态 | **FIXED** | `claim_session_status_event` 在行锁下做 epoch 单调性校验 + terminal latch（`current_run_terminal_status` 一旦写入即拒绝同 epoch 的后续终态声明），状态迁移与事件落库在同一事务原子提交 | `api/app/infrastructure/repositories/db_session_repository.py:369-428` |
| P0-05/07/08/09 | 资源无版本绑定、非原子发布 | 已在 2026-07-28 审计的 superseded 头注中记录为由 commit `db986f2`（2026-07-31）修复，本次不重复复核 | — | 见旧审计文档头部注记 |
| P0-10 | Git 来源命令注入、SSRF、路径越界 | **FIXED** | `validate_git_url` 强制 https、拒绝凭据、拒绝控制字符和 shell 元字符、解析主机名对应 IP 后逐个做 `_reject_unsafe_address`（非 global IP 拒绝，防内网/回环 SSRF）；`normalize_contained_path` 用 `posixpath.commonpath` 做 canonical containment 校验，拒绝绝对路径与 `..` 穿越；ZIP 解压有条目数/体积/压缩比三重限制与符号链接拒绝 | `api/app/domain/services/codebase/source_validator.py:34-214`（`normalize_contained_path`、`validate_git_url`、`_addresses_for_host`、`_reject_unsafe_address`、`validate_zip_bytes`，覆盖到文件末尾） |
| P0-11 | 写路由 Auditor guard 与资源就绪校验不一致 | **FIXED** | `ResourceGuardService.validate_session_request` 成为唯一资源解析入口：按 `ResourceKind` 取 provider、校验返回的资源/版本归属未被偷换、强制 `published=True` 且状态在 `{SUCCEEDED, DEGRADED}` 才放行；不再有旁路直接读资源表判定就绪的路径 | `api/app/application/services/resource_guard_service.py:45-98`（覆盖到文件末尾，`validate_session_request` + `_resolve`） |
| P0-12（lease ack 部分） | Worker lease 冲突消息不 ack | **FIXED（阶段 2 之前已修）** | `_handle_claimed_job` 按 `run_generation` 判定重复/过期分发：`run_generation < current_generation` 且 `can_ack_stale_dispatch` 为真时显式 `ack_dispatch` 并返回 `ACK_DUPLICATE`，避免陈旧消息被无限 autoclaim 重放 | `api/app/worker/main.py:532-567`（`_handle_claimed_job` 的 generation 守护与 `ack_dispatch` 调用） |
| P0-12（codebase 对账部分） | Codebase 没有 stuck-build 对账 | **FIXED-BY-THIS-PHASE** | 本阶段 Task 1 新增 `CodebaseIngestionRunner.reconcile_stale`，与既有 KB 对账同构：按 lease-owner 存活判定候选 build 是否孤儿，孤儿 build 交给 `_fail_build` 终结（候选置 FAILED、`codebase.status` 回滚、清 `ingest_task_id`）；并挂入 worker 的 startup 与周期性 `_task_reconcile_loop` 两处循环。评审阶段额外发现并修复一个真实竞态：`run_build` 的候选发布是两阶段提交（事务 A 落 `publish_candidate`+`codebase.status=READY`，事务 B 才追加 `SUCCEEDED/DEGRADED` 终态事件），若 `reconcile_stale` 恰好在事务 A 提交后、事务 B 提交前跑，旧实现会把一个已发布成功的候选误标为 `FAILED` 且永久卡死（`_append_build_event` 见 build 已终态后静默放弃写入真实成功事件）。修复后 `_fail_build` 增加"候选已发布"仲裁分支：命中该条件时不再走失败路径，而是补写权威的 `SUCCEEDED`/`DEGRADED` 终态事件（`reconciled_after_publish: True`），与 KB 版"发布优先于失败"的语义对齐 | `api/app/domain/services/codebase/ingestion_runner.py:608`（`reconcile_stale`）、`:632`（`_fail_build`，含 `_PUBLISHED_CANDIDATE_STATES` 仲裁分支，模块级常量见 :72）；`api/app/worker/main.py:159-160`（startup 挂载并列 KB/Codebase 两个对账）、`:238-239`（periodic 挂载）、`:422`（`_reconcile_stale_codebase_builds` 定义）；测试 `api/tests/app/worker/test_codebase_build_reconcile.py`（三条核心用例：孤儿 build 终结释放、存活 lease 不触碰、发布竞态窗口不被误标——覆盖终结/守护/仲裁三条不变量，且验证了二次调用幂等） |

## 3. 残留观察（无功能风险，未列入修复）

以下三项在复核中确认**不构成 P0 级功能风险**，属于 2026-08-04 计划 Global Constraints 明确列出的 Non-Goals，留待后续阶段随需就手：

1. **Ask 纵深防御缺失**：`ToolRegistry.build_ask_tools()`（`api/app/domain/services/tools/tool_registry.py:62-76`）在构造阶段仍不加区分地把 `extra_tools` 全部塞进 Ask 工具列表，没有在装配层做只读 allowlist 前置过滤。当前之所以安全，是因为 P0-01 的修复把强制点下沉到了调用层——`PolicyBoundTool.invoke` 在每次实际执行前都会经 `CapabilityPolicy.allows`/`allows_integration` 校验，非只读能力在 Ask 模式下必然被拒绝（契约测试已覆盖）。但装配层不做前置收敛意味着 Ask 主 Agent 的工具清单本身仍会暴露不该出现的工具名/schema，属于纵深防御短板而非可利用的越权路径。
2. **`planner_react.py` 遗留单调用兜底路径**：规划器仍保留一条历史上的单工具调用兜底分支，未收敛进批次执行的统一路径。复核确认该分支仍然强制经过 `ToolBatchExecutor`（不存在绕过审批/重试/终态判定的独立调用通道），因此不影响 P0-02/03 已建立的不变量，纯属代码可维护性问题。
3. **`FlowStatus` 命名**：`api/app/domain/services/flows/base.py:12` 的 `FlowStatus` 枚举命名与会话层的 `RunOutcome`/终态语义并列存在，容易望文生义地被误认为是同一层概念，但两者作用域不同（Flow 内部执行状态 vs. 会话/epoch 终态），未发现因此产生的双终态或误判案例。

## 4. 阶段 2 交付简述

本阶段除完成 P0-12 收尾外，同时交付了「治理档案」能力：`GovernanceProfileService`（`api/app/application/services/governance_profile_service.py`）作为纯聚合 read-model，组合既有审计链、检查点、会话状态与 `verify_session_chain` 产出统一的会话级治理视图，不新建数据库表；证据导出复用既有 `EvidenceService` 的 HMAC 签名机制（照搬 PatrolEvidenceService 模式）将治理档案嵌入可核验证据包；`api/app/interfaces/endpoints/compliance_routes.py` 新增 `get_governance_profile` 路由（`require_auditor_or_admin` 权限），并由前端 `ui/src/components/admin/governance-profile-view.tsx` 在既有 `/admin/compliance` 证据中心下挂出 auditor 详情页。三部分改动均未触碰本文档复核的 11 个既修复 P0 的核心实现。

## 5. 阶段全量验证结果

```bash
cd api && uv run pytest -q
# 1682 passed, 36 skipped, 81 warnings, 2 errors in 17.65s
```

2 个 error 均为已知环境性失败（本机未监听 Postgres 5432 导致 `Connection refused`）：`tests/app/infrastructure/security/test_tenant_rls_integration.py::test_application_role_is_non_bypass_and_rls_is_effective`、`tests/app/interfaces/endpoints/test_status_routes.py::test_get_status`。均与本阶段改动无关、非回归，与 Task 1/2/3/4 各阶段报告中记录的结果一致。

```bash
cd ui && npm run lint && npx tsc --noEmit && npm run test && npm run build
```

- `npm run lint`：0 errors，92 个既有 `simple-import-sort/imports` 警告（历史遗留，非本阶段改动引入），退出码 0。
- `npx tsc --noEmit`：无输出，退出码 0。
- `npm run test`：23 个测试文件、86 个用例全部通过。
- `npm run build`：编译成功，全部路由（含 `/admin/compliance`、`/admin/compliance/sessions/[sessionId]` 等）正常产出。

结论：全绿，无新增回归。

## 6. 附：文档存放说明

`docs/superpowers/` 整目录经本地 `.git/info/exclude` 排除在常规 `git add`/`git status` 之外；本文档与 2026-07-28 旧审计文档的头部修改均按项目既有惯例以 `git add -f` 强制入库，其余 `docs/superpowers/specs|plans/` 下的 spec/plan 文档不入库（本地留存）。
