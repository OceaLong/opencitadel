[English](07-approved-remediation.md)

# 审批通过后执行 Ops Patrol 修复

教程 06 只有只读巡检。本教程增加独立写路径：正式 `remediation` Run 只有在持久审批
被批准后，才能调用一个白名单 Actuator 操作。模型永远拿不到 Actuator 工具。

## 开始前

先完成[只读每日 Ops Patrol](06-ops-patrol.zh-CN.md)，再准备：

- 使用专用最小权限 ServiceAccount 的 Ops Actuator；
- 名称严格为 `ops-actuator` 的 Streamable HTTP 集成；
- 全局运行时设置 `patrol_policy.remediation=enabled`；
- 可发起和审批的 Operator；Auditor 始终只读。

Actuator 只能对已注册 Deployment/StatefulSet 执行 `get/list/watch/patch`，对
ReplicaSet 执行 `get/list`，绝不能读取 Secret 或使用 `exec`/`attach`。

## 配置 Actuator

Actuator 配置均使用 `OPS_ACTUATOR_` 环境变量前缀。最终目标边界来自白名单，而不是
提案文字。

```yaml
opsActuator:
  enabled: true
  targetRef: production-cluster-a
  allowedNamespaces: [opencitadel]
  allowedWorkloads:
    opencitadel:
      opencitadel-api:
        kind: deployment
        min_replicas: 2
        max_replicas: 10
```

以精确名称 `ops-actuator` 注册 `http://opencitadel-ops-actuator:8091/mcp`。
后端 Activity 直接调用它；不得把 Actuator 加入 Agent Skill 或模型 Tool Policy。

## 发起提案

1. 在 **Ops Patrol → Runs** 中打开可处理 Finding。
2. 选择 **发起修复**。
3. 选择 Restart、Scale 或 Rollback。Scale 要求正整数副本数；Rollback 固定回到上一
   Revision。
4. 确认精确 Workload、影响摘要与回滚说明。
5. 提交提案。

API 会持久化 `PatrolRemediation(status=proposed)`，创建关联 Session，并准入一个
`remediation` Run。在工作流发出正式审批请求前，唯一的 `remediation.execute`
Activity 不会被创建。

## 批准或拒绝

打开关联 Session。审批卡展示 Subject `remediation.execute` 与冻结 Risk Summary。

- **批准** 发出 OwnerScope 约束的 `DecideApproval` Command；Run 恢复并调度 Activity。
- **拒绝** 必须填写反馈；Command 使用 `rejected`，Run 以
  `approval_rejected` 原因取消。

审批前，本提案的 Actuator 调用次数恒为零。执行时，服务重新校验 Owner Binding、
不可变参数 Hash、实时 Capability Baseline、Action/Target 白名单与持久化修复幂等键，
随后流转 `proposed → executing → executed`，或记录稳定错误码。Activity 重复投递不能
产生第二次 Mutation。

**管理后台 → 合规** 的 Governance Profile 展示审批身份、决策人、反馈、Run/Event
链与证据。

## 验证闭环

修复执行成功后，会自动针对同一冻结 Pack 准入一个验证 Patrol Run。

- 关联 Check 通过：修复变为 `verified`，且只关闭原始 Finding。
- Check 为 Warn/Fail/Error：修复以 `recheck_failed` 变为 `failed`，Finding 保持开放。

按教程 06 验证复检 Run Evidence ZIP 的 Manifest Hash 与 HMAC。修复 Run 与审批记录在
Governance Profile 中独立可见。

在临时集群中演练：

```bash
PATROL_RUN_REMEDIATION_FIXTURE=true ./scripts/run-patrol-fixtures.sh
```

Fixture 在无 LLM 条件下验证真实 Collector、Actuator、Kubernetes Mutation、幂等重放与
复检路径。

## 安全停用

设置 `patrol_policy.remediation=disabled`。新提案与执行 Fail Closed；只读 Patrol、既有修复
Run、审批、Audit 与 Evidence 仍可读取。设置 `patrol_policy.admission=paused` 可停止全部新的
Patrol/Remediation Run，同时保留导航与历史。

参见 [Ops Patrol 架构](../architecture/ops-patrol.zh-CN.md)与
[运维手册](../operations/ops-patrol.zh-CN.md)。
