[English](07-approved-remediation.md)

# 审批通过后执行 Ops Patrol 修复

教程 06 只涉及只读检查。本教程在同一个 Pack 之上增加一条范围收窄、需人工审批的写入通道：独立的 Ops Actuator 仅执行三个注册制动作——重启、扩缩容、回滚——作用于 Kubernetes Deployment/StatefulSet，且只有在 Operator 于受治理会话内批准该具体调用后才会执行。审批会话之外没有任何代码路径能调用 Actuator。

## 开始前

需要先完成 [运行只读每日运维巡检](06-ops-patrol.zh-CN.md) 中的全部准备，并额外具备：

- 部署在目标集群中的 Ops Actuator，使用专属最小权限 ServiceAccount（仅对 Deployment/StatefulSet 有 `get`/`list`/`watch`/`patch`，对 ReplicaSet 仅 `get`/`list`——绝不涉及 Secret、`exec`、`attach`）；
- 已注册为 Streamable HTTP MCP Server、名称严格为 `ops-actuator` 的 Actuator（执行服务按这个固定名称解析；与按 Pack 绑定的 Collector 不同，每个平台部署只有一个 Actuator）；
- 内置 `ops-patrol-remediation` Skill——API/Worker 启动时自动 Seed，无需手工注册；
- 除教程 06 的 `enable_ops_patrol` 外，还需要管理员启用全局功能开关 `enable_ops_patrol_remediation`；
- 可以发起修复提案的 Operator 权限。审批发生在提案创建出的会话内，因此审批人需要拥有该会话的正常访问权限；Auditor 事后可复核结果，但不能发起或审批。

仅验证本地传输时可以启动：

```bash
docker compose --profile actuator up -d --build opencitadel-ops-actuator
```

Compose Profile 与 Collector 一样不会挂载宿主机 kubeconfig。真实 Kubernetes 写操作应使用 Helm/Kustomize ServiceAccount，且绝不能将 Actuator 暴露到公网。

## 准备 Actuator

配置仅通过环境变量（`OPS_ACTUATOR_` 前缀）。下表中的白名单是每次写调用真正的门禁——无论会话提案什么内容，未列入白名单的 Workload 都会在任何 Kubernetes 调用前被拒绝：

| 变量 | 默认值 / 范围 | 用途 |
|------|---------------|------|
| `TARGET_REF` | `opencitadel-local` | Pack 匹配的稳定身份标识 |
| `ALLOWED_NAMESPACES` | `["opencitadel"]` | 非空 Namespace 白名单 |
| `ALLOWED_WORKLOADS` | `{}` | Namespace → Workload id → `{kind, min_replicas, max_replicas}` 的 JSON 映射；未列入的 Workload 不能被任何写动作定位 |
| `TRANSPORT` | `streamable-http` | `streamable-http` 或仅限开发用的 `stdio` |
| `CONCURRENCY` | `4`，范围 1–8 | 同时进行的写动作上限 |

完整参考（含输出上限）见 [Ops Actuator README](../../ops-actuator/README.zh-CN.md#配置参考)。

Helm Values 示例：

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

在 **设置 → 集成** 中，把内部 URL（默认 Helm Release 为 `http://opencitadel-ops-actuator:8091/mcp`）注册为 Streamable HTTP、启用，并命名为 `ops-actuator`。与 Collector 不同，Actuator 的写工具从不暴露给模型——后端执行服务直接调用它们——因此这次注册不需要额外持久化 Tool Policy Payload。

管理员打开 **设置 → 运行时 → feature_flags**，启用 `enable_ops_patrol_remediation`。

## 发起修复提案

1. 打开有可处理 Finding 的 Run，进入 **Ops Patrol → Runs → {run}**。
2. 在 Finding 卡片上点击 **发起修复**。只有依赖 `k8s_*` 探针的 Finding（工作负载可用性、重启突增）才提供自动化动作；HTTP、证书、备份、依赖等探针的检查项会显示"该检查项没有可用的自动化修复动作"。
3. 选择动作——**重启工作负载**、**调整副本数**或**回滚工作负载**。`调整副本数` 要求正整数目标副本；`回滚工作负载` 可选填目标版本号。
4. 确认或填写 Workload 名称。如果该检查探针从未记录过 Workload，此字段为必填才能提交。
5. 勾选 **我已阅读影响面说明与回滚方案，确认可以执行此修复**，然后点击 **发起修复并打开审批会话**。

提交后会创建一条 `PatrolRemediation` 记录（状态 `proposed`），并打开一个运行内置 `ops-patrol-remediation` Skill、`gate_profile=strict` 的新会话，页面会自动跳转到该会话。

## 审批与执行

新会话只有一个工具可用——`patrol_execute_remediation`，其审批模式声明为 `always`：任何调用在真正运行前都必须先经过人工决策，会话内没有 MCP、A2A、记忆或任何其他工具访问权限。Agent 自己的这一轮对话会在会话记录中先陈述动作、目标、影响面说明与回滚提示，再调用一次工具；审批卡片本身只展示原始工具调用，因此这段自然语言说明请在对话记录中查看。

调用到达时，会话会展示一张 **工具操作需要审批** 卡片，包含：

- 工具名 `patrol_execute_remediation`；
- 调用参数的原始 JSON 预览；
- **批准** 与 **拒绝** 按钮。

点击 **拒绝** 会打开必填的拒绝原因输入框；确认后拒绝结果会作为工具结果返回给 Agent。由于这个 Skill 被限定只能调用这一个工具，会话会自然走向它自己的正常结束，而不是被强制杀死。一旦会话到达任意终态，仍处于 `proposed` 的修复记录会被自动转为 `cancelled`，`error_code=SESSION_TERMINATED`——这条路径上 Actuator 始终没有被调用过。点击 **批准** 则放行该调用。

**在你做出决策之前，本次提案对 Actuator 的调用次数恒为零。** 该工具的审批模式是 `always`，批执行器只会把调用排队，绝不会在人工决策前调用它——放弃或拒绝会话都会让 Actuator 保持未被触碰的状态。批准后，服务会重新校验提案的参数 Hash，确认 Actuator 的实时 Capability Hash 仍与本会话构建时捕获的基线一致（漂移则拒绝），然后使用该修复记录自身持久化的幂等键——绝非工具调用本身携带的值——恰好调用一次 Actuator。修复记录随之从 `executing` 流转到 `executed`；若以上任一校验或 Actuator 调用本身失败，则流转到 `failed` 并带有稳定错误码。

要查看这一具体会话的完整治理档案——审批决策、工具调用链、证据完整性——切换到 **管理后台 → 合规 → {会话}**（`/admin/compliance/sessions/{sessionId}`）。

## 复检闭环

`executed` 状态的修复会自动对同一个 Pack 触发一次新 Run（`trigger_type=remediation`），无需手动重新运行。可从原 Run 详情页的 **修复记录** 区块的 **查看复检 Run** 链接打开。

- 若复检 Run 中对应检查项转为通过，修复记录变为 `verified`，原 Finding 自动被标记为已处理（`decided_by=system:remediation`），决策原因引用该修复记录与复检 Run。
- 若检查项仍然失败或告警，修复记录变为 `failed`（`error_code=recheck_failed`），Finding 保持开放、等待人工决策。

下载复检 Run 的证据 ZIP，按教程 06 相同的方式验证其 SHA-256 Manifest 与 HMAC——修复触发的复检产生的就是普通的巡检证据，没有单独的导出路径。发起修复的会话本身另有独立的证据与审批记录，可从上文的治理档案页面查看。

要在没有真实集群故障的情况下在本地演练整个闭环，可在运行 `./scripts/run-patrol-fixtures.sh` 前设置 `PATROL_RUN_REMEDIATION_FIXTURE=true`。Fixture 21（`fixture-remediation-crashloop`）会注入一个有界崩溃循环，预期 `restart_workload` 依次经过 `proposed → executing → executed → verified`，复检时 `k8s-workload-availability` 与 `k8s-restart-spike` 均转为通过。

## 安全回滚

在全局运行时设置中设 `feature_flags.enable_ops_patrol_remediation=false`。新提案会被立即拒绝——`propose()` 在触碰任何数据前就已按此开关拒绝——而教程 06 的只读巡检、既有修复历史与证据仍完全可读。重新启用不会丢失任何 `PatrolRemediation` 记录。

关闭 `enable_ops_patrol`（教程 06 的开关）同样会停止修复功能，因为提案与执行都依赖同一套 Patrol Run/Finding 机制。

信任边界与状态机见 [Ops Patrol 架构 — Remediation](../architecture/ops-patrol.zh-CN.md#remediation)，部署与证据细节见 [Ops Patrol 运维手册](../operations/ops-patrol.zh-CN.md)。
