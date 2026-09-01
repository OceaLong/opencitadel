# 技术决策

[English](technical-decisions.md)

本文记录当前架构，不描述升级路径。

## 1. 模块化 Python 后端

FastAPI、Pydantic 与 SQLAlchemy 位于明确的 interfaces/application/domain/infrastructure
边界内。手工强类型装配分别构建 `ApiRuntime` 与 `KernelRuntime`；Import 不分配资源，HTTP
依赖只从 `app.state` 解析，每个进程只加载一次部署配置。Import Contract 保证 Application
不依赖 Infrastructure，并阻止 Service Locator 与全局 Getter 回归。

## 2. 显式事务与 post-commit 提示

Unit of Work Context 未显式调用 `uow.commit()` 时一律 rollback，即使通过正常 return 退出。
Application Mutation Method 决定是否提交，Repository 永不提交。Redis 发布只能发生在
post-commit 阶段，因此 Redis 故障最多增加延迟，不能改变 PostgreSQL 权威结果。

## 3. PostgreSQL 事件溯源执行

每个 Agent、Ask、资源构建、自动化、巡检与修复行为都是强类型 Run。追加式、哈希链执行
事件是唯一生命周期事实。同一个 PostgreSQL 提供 Command Inbox 幂等、持久 Activity、Timer、
Outbox、完整性校验 Snapshot 与可重建投影。

这在保持自托管组件精简的同时，把进程死亡、重复投递、重连和 Redis 丢失变成显式恢复场景。

## 4. Redis 是传输，不是状态

Redis 只提供唤醒提示、缓存与熔断协调。内核始终轮询 PostgreSQL pending 行。Run 状态、
Activity 结果、审批与 Cursor 都不以 Redis 为权威。

## 5. 持久 Activity 边界

LLM、检索、解析/索引、沙箱、浏览器、MCP、A2A、存储和 Actuator 均为非确定性工作，
必须作为 Activity 运行。Provider 调用前先持久化 Invocation Intent 与 call-start。Generation
Fencing 拒绝过期完成；非幂等未知结果等待显式 Operator 处理。

## 6. 不可变资源发布

知识库摄取构建不可变 Candidate。发布前验证完整闭包，并 CAS 更新 Active Version。
Session 绑定具体已发布版本，因此后续构建不会改变已有 Run 的证据边界。

## 7. 强制租户隔离与最小权限

Owner Scope 表同时使用应用过滤与 PostgreSQL FORCE RLS。API、执行内核、Migration、Bootstrap
角色彼此独立，运行时角色不拥有 Schema。Event Store 还会在建 Stream 时冻结 OwnerScope，
拒绝 Scope 不一致的 Append。

## 8. 沙箱工具与窄化运维平面

Browser、Shell 与文件工具运行在有资源上限和受控 Egress 的 Docker/Kubernetes 沙箱中。
Compose 由 Broker 独占 Docker 访问。Ops Collector 只读；Ops Actuator 只暴露闭合集合写动作，
且只能通过策略检查与持久审批到达。

## 9. 共享对象存储

Artifact、Attachment、大 Activity 输入/结果、源材料 Snapshot 与证据包使用共享对象存储。
生产支持 COS 或 S3 兼容 MinIO。数据库保存 Reference 与 Digest，不存无界 Provider Payload。

## 10. Next.js 投影客户端

Next.js UI 提交 Command 并展示正式投影。SSE 实时流与回放使用同一公开事件契约。
浏览器状态不决定 Run 转换；审批动作针对持久 Approval Batch。认证资源缓存由 Provider
持有并按 `userId + workspaceId` 隔离，Generation Invalidation 阻止晚到响应跨身份或工作区。

## 11. 版本化 Secret 信封

LLM 与集成 Secret 使用 `v2.<key-id>...` Fernet 信封。当前 Key 写新值，显式 Previous-Key
Ring 支持计划内轮换；审计签名有独立 Key Ring。明文凭据不是受支持的持久格式。

## 12. 单一全新 Schema

Alembic 只包含当前 Catalog 的一个 Initial Revision。项目不提供执行历史转换、备用事件 Schema
或 Engine 间运行时路由。首个受支持生产版本建立升级契约前，结构变更直接更新全新 Schema。
