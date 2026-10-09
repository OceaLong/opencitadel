# 治理平面

[English](governance-plane.md)

治理是 Run/Activity 协议的一部分。模型可以提出 Tool Call，但不能自行暴露、批准或执行能力。

## 端到端路径

![governance sequence](../assets/diagrams/governance-sequence.png)

## 能力收窄

一个可调用工具必须通过全部边界：

1. 平台注册与明确 `ToolExecutionPolicy`；
2. 认证 Role、OwnerScope 与 Operator Domain；
3. Run Family/Mode；
4. 已选 Skill `allowed_tools` 与 MCP/A2A Server Ref；
5. Model 调用前的 Exposure Filter；
6. Activity 执行时再次 Lookup 与 Policy Validation。

缺失 Policy 默认采用 `capability=unknown`、`effect=interactive`、
`idempotency=unknown`、`approval=always`。Skill 只能收窄已有权限。

## Effect 与审批契约

Policy 声明 Capability、Effect（`read_only`、`workspace_write`、`external_write`、
`interactive`）、Idempotency、Approval Mode 与 Concurrency Group。Model-call Activity 把
服务端得出的 `requires_approval` 与 Risk Summary 写入持久 Model Result；纯 Agent Decision
验证这些字段后才能请求 Tool Activity。

Approval 是带稳定 Run/Approval/Subject Activity 身份的正式 Event 与 Projection。Decision
Endpoint 记录 Actor、Status、Time 与 Feedback。Reject、Expiry、Cancellation、重复 Decision、
错误 Owner 的 Decision 都不会调用 Provider。

审批是闭环，而非无限等待：

- **收件箱。** `GET /api/approvals?status=pending` 列出调用方的待审批（也可选
  `approved`/`rejected`/`cancelled`/`expired`），范围限定于当前个人/团队工作区。省略 `status`
  返回全部状态；分页使用 `limit`（默认 50、最大 200）与 `offset`。专用决策入口是
  `POST /api/approval-batches/{approval_id}/commands/decide`。
- **通知。** Run 触发 `ApprovalRequested` 时，正式投影器发送持久通知，审阅者无需轮询即被提醒。
- **超时。** 请求审批时会调度一个持久的自取消超时命令。触发后审批进入终态 `expired`
  （`ApprovalExpired` Event），Run 离开等待状态，且绝不调用 Provider。窗口由 Operations
  Policy 的 `approval.ttl_minutes` 字段（默认一天）控制，而非环境变量。到期以
  `approval_expired` 原因取消 Run。

## Invocation 安全

每个 Tool Request 都有唯一 Activity/Invocation Identity。两次有意的同参调用不会合并为一个
Invocation。Claim Generation 隔离过期 Worker。外部 Effect 不确定的调用在 Crash 后不会盲目
重试，而进入显式 Unknown-Outcome Resolution。物理容量的 Unknown Outcome Hold 在
Reconciliation 确认结算前持续保留；UI 超时不能释放它。

Argument 与大 Result 使用 Object Reference/Digest。Public Event 只包含有界脱敏 Summary。
Workspace Write 位于 Session Sandbox；External Write 在 Provider 支持时仍使用其 Idempotency Key。

## 证据

正式 Run、Approval 与 Activity Projection 构成 Governance Profile。独立 Audit Hash Chain
记录 User/Admin Action 与 Policy Denial。Evidence Export 确定、脱敏、有 Manifest 且签名。
Pending 或 Rejected Approval 不会显示成成功 Tool Execution。

参见[执行内核](execution-kernel.zh-CN.md)、[安全模型](security-model.zh-CN.md)、
[管理员与合规](admin-auditor-compliance.zh-CN.md)和[Ops Patrol](ops-patrol.zh-CN.md)。
