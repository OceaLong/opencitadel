# Plan B · 后端功能闭环（Phase 1，预估 3–5 天）

前置：Plan A 完成（尤其 A-3 密钥统一、A-4 配额下沉）。

## B-1 A2A 任务生命周期闭环

**问题**：`tasks/get`/`tasks/cancel` 已实现，但 `message/send` 响应不含 taskId/contextId（`api/app/application/services/a2a_server_service.py:53-63` `build_a2a_text_response`），外部客户端拿不到 id → 新功能不可达；且每次 send/stream 都新建 session（`:181`、`:226`），`params.contextId`/`taskId` 被忽略 → 只能单轮；`tasks/cancel` 无条件返回 `"canceled"` 不校验实际状态（`:346-347`）。

**修法**：
1. `message/send`/`message/stream` 响应按 A2A 协议返回 Task 对象（含 `id`=session_id、`contextId`、`status`），流式场景在首个事件带上。
2. 多轮：`params.contextId` 命中已有 session 时复用（校验属主与状态），未命中才新建；`taskId` 语义与 `tasks/get` 对齐。
3. `tasks/cancel`：`stop_session` 后读取 Run 实际状态，只有进入 CANCELLED 才返回 `canceled`，否则返回当前状态。
4. 契约测试：用 A2A JSON-RPC 客户端视角写"send → get → 多轮 send → cancel"全链路测试。

**验收**：契约测试全绿；外部 A2A 客户端（可用 e2e 里现有 fixture）能完成多轮对话并取消任务。

## B-2 审批域三连修

1. **TTL 旋钮生效**：把 `OperationsPolicy.approval.ttl_minutes`（`domain/runtime_policy/operations.py:53`）thread 进 `RequestApproval`（`domain/execution/run.py:41-46`），替换硬编码 1440。注意 Revision 生效时机：取 Run 创建时的 active revision，写进 Run 快照，避免中途改策略影响在途审批。
2. **团队审批通知**：`postgres_formal_projector.py:276-280` 对纯团队作用域 Run（`owner_user_id is None`）直接 return——改为通知团队全部具备审批权限的成员（或 owner/admin 角色），并写单测覆盖"团队 Run 审批必有至少一个接收人"。
3. **过期处置显性化**：`run.py:711-713` 过期审批按 rejected → CANCELLED，至少要发一条"审批超时未处理"通知给发起人+审批人；升级/改派机制可留作后续（在 spec 记录，不在本期）。

**验收**：改 TTL 策略后新 Run 的审批过期时间随之变化（单测+手测）；团队 Run 触发审批时通知表出现记录；超时取消有通知。

## B-3 回收站保留期自动清理

- 在 `job_scheduler.py` 的 `_run_scheduler_leader_tick`（`:283-360`）新增 purge tick，模式照抄现有 KB 版本 GC / Patrol 保留期清理两个同构 tick。
- 实现 `session_service`/`knowledge_base_service` 的批量 purge（复用现有手动 purge 逻辑，`session_service.py:185`、`knowledge_base_service.py:1010` 的 TODO(recycle-bin) 即挂载点），每 tick 限量（如 100 条）防长事务。
- 保留期从配置读取（默认 30 天，进 `.env.example` 并注明），purge 动作写审计日志（合规域要求删除可回溯）。

**验收**：把保留期调成 0 天的集成测试：软删 → tick → 数据物理消失 + 审计记录存在。

## B-4 Patrol 外部通知渠道

- PatrolPack 配置模型增加 `notify_channels` 字段（结构对齐定时任务已有的通道定义，见 `scheduled_job_service.py:360/507` 对 `notification_service.dispatch_notify_channels` 的用法）。
- `patrol_run_service.py:643` 巡检完成时除站内信外，按 pack 配置分发 webhook/email/MCP-IM。
- API schema、OpenAPI 契约、前端生成类型同步再生成（`scripts/generate-api-contract.mjs`），前端表单接入归 Plan C-3。

**验收**：配置 webhook 通道的 pack 跑完一次巡检，webhook 收到 payload；未配置通道行为不变。

## B-5 Web 搜索引擎替换

**问题**：`infrastructure/external/search/bing_search.py:17-70` 硬编码 CSS 类名爬 Bing HTML，无 key、无降级提示，失败静默返回空 → Agent 拿空结果继续作答。

**修法**：
1. 抽象 `SearchProvider` 接口，默认实现改为可配置 API：首选自托管 SearXNG（无 key、可进 compose `local` profile），并提供 Tavily/Bing Web Search API 两个 key-based 实现。
2. 环境变量：`SEARCH_PROVIDER`（searxng|tavily|bing_api|none）+ 对应 endpoint/key，进 `.env.example` 与 capability 探测（`capability_service`），未配置时 `GET /api/capabilities` 如实上报 DISABLED，Agent 工具目录里搜索工具不注册（而不是注册了返回空）。
3. 失败时抛出可观测错误进执行事件流，禁止静默空结果。

**验收**：`SEARCH_PROVIDER=none` 时 Agent 无搜索工具；配置 searxng 后会话内搜索返回真实结果；provider 故障时执行事件流出现明确错误。

## B-6 顺手项（小，随本期一起）

- 死代码处理：`domain/services/video_service.py`（零调用方，删或建 issue 挂工具）、`application/data/nutrition_foods.json`、空包 `infrastructure/external/message_queue/__init__.py`、`infrastructure/external/task/__init__.py`。
- API 进程内永不 warm 的 `SandboxPool`（`composition/kernel.py:244` 只在 kernel 启动 `pool.run`）：API 进程改为直连 `create_unpooled` 语义或显式禁用池，消除"看似有池实则空转"的陷阱。
- Service API Key 轮换接口：对齐定时任务的 `rotate-secret` 模式（`scheduling_routes.py:137`），给 `service_api_key_routes.py` 加 rotate 端点。

## 执行顺序建议

B-1（独立）与 B-2（独立）并行 → B-3 → B-4 → B-5 → B-6。每项完成后暂存+patch 备份；B-4 完成后通知 Plan C 更新前端类型。
