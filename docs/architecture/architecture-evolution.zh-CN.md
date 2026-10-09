# 架构演进与扩展

[English](architecture-evolution.md)

OpenCitadel 从模块化部署起步：PostgreSQL、可丢失 Redis Wake-up、无状态 API Replica 与可水平
扩展 Execution-Kernel Replica。扩展边界跟随持久 Queue，而不是产品领域进程。

## 当前扩展模型

- API Replica 按 Request Latency/CPU 扩展，共享 PostgreSQL/Object Storage。
- 执行内核通过 Database Fencing Claim Command、Activity、Timer、Outbox 与 Projector Work。
  扩容主要看 Pending Age、Throughput、Provider Latency 与 Sandbox Capacity，而不是只看 CPU。
- Sandbox 按 Node/Resource Admission 独立扩展。
- PostgreSQL 需监控 Connection、Storage、Lock、WAL、Backup，并验证 Restore。
- Redis 可为可用性做 Cluster，但其丢失不需要恢复执行正确性数据。
- Ops Collector/Actuator 只在其窄化安全角色内扩展。

当前 Kernel 还监督独立的评测调度、协调、评分与清理循环，以及比较交付物 Diff 和导出
Worker。这些是 `KernelRuntime` 内部独立 Fencing 的 Consumer，不是独立服务，也不形成
另一套 Run 权威。关键评测循环失败会撤销 Kernel Readiness，不会把已失效 Consumer 标为就绪。

## 演进不变量

未来引入 Queue、Workflow Engine 或服务拆分都必须保留：

1. 单一权威 Run Event Stream 与强类型 Command Idempotency；
2. 每次外部调用前先持久 Invocation Intent/Call-start；
3. 精确 OwnerScope 与最小权限数据库/服务角色；
4. Approval 是持久 Command/Event，不是 Transport Message；
5. Live/Replay 读取同一脱敏 Public Projection；
6. 不可变 Resource Version 与冻结 Session Binding；
7. 重复 Delivery、进程死亡下的确定性恢复。

## 拆分条件

只有负载或信任边界要求独立扩展时才拆组件。自然候选是 Provider-specific Activity Worker、
Projector Fleet、Object Processing 与 Sandbox Broker。拆分通过注册 Activity Contract 与 Object
Reference，不能创建第二生命周期表。

Kafka 或托管 Workflow Service 是可选项，可改善 Transport/Operations，但不能替代领域
Idempotency、Approval、Unknown Outcome、Event Integrity 或 Tenant Isolation。

## 容量信号

监控最老 Pending Command/Activity/Timer/Outbox、Claim Expiry、Projector Lag、Database
Saturation、Object-Store Latency、Provider Quota 与 Sandbox Admission。持续增长的持久 Pending
Age 是主要 Backpressure Signal。Autoscaling 必须有硬 Concurrency 与 Provider Rate Limit，
避免放大故障。

分析和导出另外限制 Source Capture、分页大小、捕获字节数与每个调用者的活跃 Job。
评测 Admission 组合持久 Execution Slot、Environment Lease 与物理模型请求预算 Bucket。
释放执行容量与结算计费义务是不同操作；未知外部效果保留 Hold，直到受支持的处理完成。

代码中的容量上限不等于全规模验收通过。AC21 参考环境验收仍未完成：多轮来源清理/复用
与原生客户端采集尚未完整接线。参见当前[参考协议与剩余工作](../../scripts/execution_capacity/REFERENCE.md)。
