# 自动化与 Scheduler

[English](automation-scheduler.md)

Scheduled Definition 是产品记录；每次 Firing 都是正式 Automation Run，并准入关联 Agent
或 Patrol Run。

```mermaid
flowchart LR
  Cron[Cron / Interval] --> Leader[Leased Scheduler Tick]
  Webhook[Signed Webhook] --> Service[ScheduledJobService]
  Manual[Manual Trigger] --> Service
  Leader --> Service
  Service --> DB[(Job Row + Session + Run Command)]
  DB --> Kernel[Execution Kernel]
  Kernel --> Automation[Automation Run]
  Automation --> Child[Agent / Patrol Child Run]
  Child --> Projection[Run Projection]
  Projection --> Reconcile[Job Summary + Notification]
```

Scheduler Loop 位于执行内核副本。短 Redis Leader Lease 只减少重复 Poll，不是正确性状态。
Database Row Lock、确定性 Firing ID、Command Idempotency 与 Active-Run Projection 共同防止重复
Admission。Redis 丢失只会让另一副本开始 Poll。

## Trigger

- Cron/Interval Tick 使用计划 `next_run_at` 生成 Firing ID。
- Manual Trigger 使用新的显式 Firing ID。
- Webhook 校验 `HMAC-SHA256(raw_body, secret)`，并由 Body/时间窗口生成 Firing ID。
  Secret 以版本化加密信封存储，仅在创建/轮换时显示。
- Patrol 绑定 Job 准入 Patrol Run；通用 Job 创建 Session 与 Automation Run，并关联 Agent
  Child Run。

Command Transaction 提交前验证 Resource Access 并绑定具体 Active Version。Job 已有活动正式 Run
时不再次准入。

## 状态与恢复

`last_run_*` 只是查询 Summary；`last_execution_run_id` 关联权威 Run Projection。Reconciliation
把 Terminal Run State 投影到 Summary，并发送持久 Inbox Notification 与可选 MCP IM。进程死亡
不会制造 Terminal State。

`GET /api/scheduled-jobs/{job_id}/runs` 返回该 Job 的分页触发历史（每次触发的 id、关联执行
Run 与终态），运维可审计每一次过往触发，而不仅是最新 Summary。Leader Lease 在副本持有期间
持续续约，健康的 Leader 持续轮询而无需反复重新获取；丢失 Lease 只会让另一个副本接管。

同一个 Leased Loop 还运行有界 Knowledge Version GC 与 Patrol Retention。它们使用独立
数据库 Lease，且不会删除 Active/Bound Version 或 Audit Row。

调度准入、轮询、Lease、并发与 Webhook 幂等配置位于 Operations Policy `scheduler`；Job Definition 的 UI 入口为 `/automation`。
