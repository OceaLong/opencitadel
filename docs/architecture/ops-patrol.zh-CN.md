# Ops Patrol 架构

[English](ops-patrol.md)

Ops Patrol 把只读采集、确定性断言与审批制修复分离。巡检与修复都使用通用执行内核，
不存在私有任务生命周期。

```mermaid
flowchart LR
  Pack[Versioned Patrol Pack] --> Admit[Patrol Run Admission]
  Admit --> Run[Patrol Run]
  Run --> Activity[patrol.execute Activity]
  Activity --> Collector[Read-only Collector]
  Collector --> Validate[Manifest + Schema Validation]
  Validate --> Assert[Deterministic Assertion]
  Assert --> Finding[Finding + Signed Evidence]
  Finding --> Proposal[Remediation Proposal]
  Proposal --> Child[Remediation Child Run]
  Child --> Approval[Persisted Approval]
  Approval --> Remediation[remediation.execute Activity]
  Remediation --> Actuator[Narrow Ops Actuator]
  Actuator --> Verify[Verification Patrol Run]
```

## Patrol Pack 与采集

Pack 发布后不可变，Snapshot 包含 Assertion、Target、Collector Server ID、Capability
Manifest/Hash、Timeout 与 Retention Policy。Admission 把 Snapshot 冻结进 Patrol 产品 Run 与
正式 Run Input。Collector Output 必须匹配注册的闭世界 Schema 与冻结 Capability Hash。

Collector 只负责 Kubernetes/HTTP/Prometheus/Certificate/Backup/Dependency 读取，只接受已配置
Name/Destination。内核在确定性服务端断言前验证每个 Submission；LLM 输出不能决定
Pass/Warn/Fail。

`PatrolExecutionActivityHandler` 是 Idempotent：Finalization 使用 Run Submission Key，只创建
一份 Report/Finding Set。Evidence Reference 与 Digest 写入后 Activity 才报告成功。

## 修复

Finding 可由固定 Action Policy 生成 Remediation Proposal。Proposal 成为关联 `remediation`
Run，其唯一 `remediation.execute` Activity 总是要求正式审批。Approval 冻结 Subject 与 Risk；
只有专用 Approval Command 能推进。

Actuator 只暴露注册的 Restart/Scale/Rollback 类操作，受明确 Namespace/Workload Allowlist。
它使用独立 ServiceAccount、NetworkPolicy、Non-root/Read-only Container 与 Idempotency Key，
不能读取应用凭据，也不能发任意 Kubernetes Call。

执行后由关联 Verification Patrol Run 判断 Finding 是否解决。Remediation Status 从这些持久 Run
投影，不从 Transport Success 推断。

## 安全不变量

- Collector 没有写 RBAC；Actuator 没有任意读写 API。
- Capability Drift、Owner Mismatch、未注册 Target、无效 Evidence 或缺失 Approval 一律关闭失败。
- Rejected/Cancelled/Expired Approval 产生零次 Actuator Call。
- 重复 Trigger、Activity Delivery 或 Completion 不会制造第二份 Finding Set 或 Mutation。
- Audit/Evidence Row 比产品 Retention 更长；Cleanup 只删除 Policy 允许的过期产品 Reference。

参见[治理平面](governance-plane.zh-CN.md)、[安全模型](security-model.zh-CN.md)与
[Patrol 运维](../operations/ops-patrol.zh-CN.md)。
