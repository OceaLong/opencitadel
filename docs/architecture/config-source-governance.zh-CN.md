# 配置来源与治理

[English](config-source-governance.md)

每个值只有一个权威来源。

| 类型               | 权威                            | 示例                                                                                                                                                                                                                                                       |
| ------------------ | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 部署拓扑与 Secret  | 环境变量或 Secret Manager       | 数据库身份、签名/加密 Key（`API_KEY_SECRET`、`AUDIT_SIGNING_KEY`、`JWT_SECRET`/`JWT_PREVIOUS_SECRETS`、`DATABASE_AUTHORIZATION_SIGNING_SECRET`、`SANDBOX_TOKEN_SEED`、`OPS_ACTUATOR_TOKEN`/`OPS_COLLECTOR_TOKEN`）、OAuth、存储、沙箱 Driver/Image/Network |
| 实时领域行为       | PostgreSQL Runtime Policy Head  | Agent 限制、超时、重试、调度、沙箱限制、Knowledge/Patrol 保留                                                                                                                                                                                              |
| Integration        | Owner Scope PostgreSQL Resource | 推理 Endpoint/Model/Binding、MCP、A2A、Skill                                                                                                                                                                                                               |
| 产品数据           | 领域表                          | Session、Job、Pack、Resource、Version                                                                                                                                                                                                                      |
| 进程安全与维护限制 | Deployment Settings             | Execution 容量/背压、Poison Claim 上限、Queue/Recycle-bin 保留、Evaluation Environment 容量                                                                                                                                                                |

系统不存在运行时 YAML Overlay、User Behavior Override 或 Policy Field 的环境变量回退。
Migration 创建一个类型化 Execution Policy Revision、一个类型化 Operations Policy Revision，
并通过单个原子 Head 同时激活两者。后续变更只能由 Admin API/UI 创建不可变 Revision。

## Runtime Policy 边界

- Execution Policy 在 Run 准入时冻结；进行中的 Run 始终使用原 Revision 与 Policy Snapshot。
- Operations Policy 控制实时准入、流量、调度、沙箱创建、巡检姿态、Source Access、GC 与 Retention。
- Repository Refresh 校验 Head/Revision 配对、Schema Version 与 Digest；Fresh Consumer
  约束已验证缓存年龄。Integrity/Persistent Policy Error 与 Stale State Fail Closed；
  临时存储故障可在最大 Staleness Window 内使用最近已验证 Pair，Readiness 同时报告不可用。
- 更新使用 Head Version Compare-and-swap；冲突时保留 Admin Draft，必须显式 Reload 或 Retry。
- Restore 会创建新 Revision；历史永不原地修改。

## 部署与 Integration 边界

Deployment Settings 描述进程拓扑与运行安全/维护限制，不复制领域 Policy Field。
Sandbox Topology 属于部署；
每个认证 Create Request 携带活动 Operations Policy Revision 与资源限制。

Inference、MCP、A2A 是一等 Owner Scope Resource。Credential 使用版本化加密信封存储，读取时脱敏。
Skill、Automation 与执行请求通过稳定 ID 绑定；Display Name 不是身份。

签名与 Token Secret 采用 Active/Previous Ring，可零停机轮换：
`API_KEY_SECRET`/`API_KEY_PREVIOUS_SECRETS`、
`AUDIT_SIGNING_KEY`/`AUDIT_PREVIOUS_SIGNING_KEYS`、
`JWT_SECRET`/`JWT_PREVIOUS_SECRETS`。`DATABASE_AUTHORIZATION_SIGNING_SECRET` 可选地将数据库
授权 HMAC 与 `SESSION_SECRET` 拆分，未设置时回退到它。`SANDBOX_TOKEN_SEED` 派生每沙箱数据面
Token，`OPS_ACTUATOR_TOKEN`/`OPS_COLLECTOR_TOKEN` 守卫 Ops MCP Server。执行内核高级调优
（`EXECUTION_ACTIVITY_MAX_CONCURRENCY`、`EXECUTION_ACTIVITY_BATCH_SIZE`、
`EXECUTION_IDLE_POLL_SECONDS`）保留在环境变量。当前 Settings 同样持有
`EXECUTION_MAX_ACTIVE_RUNS_PER_SCOPE`（默认 200）、Claim Attempt 上限、Execution Queue
Retention/Purge Batch，以及 `RECYCLE_BIN_RETENTION_DAYS`（默认 30）；这些是明确的
运行限制。Knowledge Version GC 与 Patrol Retention 仍由 Operations Policy 管理。

## 变更规则

- 新增 Policy Field 时，只修改类型化 Model、初始 Seed、Admin Form、OpenAPI Contract、Test 与 Runtime Policy 架构/运维文档。
- 新增 Deployment Setting 时，只修改 `core/config.py`、`.env.example`、Compose、Helm、Validation Schema 与部署文档。
- 禁止在权威来源之间复制同一个值，也禁止引入 Fallback Path。
- Secret 不得进入 Runtime Policy Revision、Run Public Projection 或 UI Event Payload。

Revision、一致性与 Consumer Model 见 [Runtime Policy 控制面](runtime-policy-control-plane.zh-CN.md)。
