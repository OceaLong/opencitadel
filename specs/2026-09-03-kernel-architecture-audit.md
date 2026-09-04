# 执行内核与可插拔扩展 · 架构级审计报告（2026-09-03）

审计对象：`api/app/domain/execution`（事件溯源领域层）、`api/app/application/execution`（决策/活动/收发件箱/定时器运行时）、可插拔扩展面（decisions / activity_registry / tools / skills / MCP / A2A / LLM & search 工厂）、投影与分层边界、`composition/kernel.py` 装配、双进程共享组件。四个并行深读代理全量通读约 1.4 万行内核代码，最高严重度发现已人工抽查证实（file:line 均已核对）。

## 总体结论

骨架质量高于典型自研同类：纯函数聚合 + hash 链完整性 + 双层乐观并发 + 确定性快照 + gapless 水位；投递语义为 at-least-once 传输 + inbox 层 exactly-once effect，decision→事件→outbox 严格同事务（无双写窗口）；所有队列统一 `FOR UPDATE SKIP LOCKED + claim_generation fencing + 30s 租约`，多副本可安全并行；正向分层纪律（application 零依赖 infrastructure）有 import-linter 强制。

四条主线风险：

1. **可用性击穿链**：决策源饿死（P0）、活动面无异常隔离、滚动发布杀死在途 Run、工具异常升格为整 Run 失败——每一条都会在日常运行/运维中真实触发。
2. **schema 演进半吊子**：upcast 只接通命令重放路径，projector 裸吞事件、internal_payload 无演进通道、RunCreated 已用"重定基线"绕过自家规范——第一次真正升版事件会三处同时断裂。
3. **可插拔名不副实**：四个扩展面四种注册模式，决策↔活动靠重复魔法字符串耦合、无启动期交叉校验；工具装配存在一真一死两份手写清单（Vision 工具因此在生产静默失踪）。
4. **观测闭环断裂**：精心定义的 inbox 深度/outbox lag/投影 lag 指标从未刷新（恒为 0），投影故障用户与运维双向静默。

---

## P0（会造成面上停摆，应最先修）

### P0-1 决策源无"就绪"过滤 → 等待型 Run 挤占批次，新 Run 决策饿死
- `postgres_run_decision_source.py:40-54`：唯一过滤 `terminal=False` + 未隔离，`ORDER BY updated_at ASC LIMIT 100`。
- `decisions/base.py:61-77`：WAITING(approval) 和 RUNNING 且有在途活动的 Run 返回 idle——不需要决策却占名额。
- 机理：等待审批的 Run 不再追加事件，`updated_at` 停在旧值永远排在窗口最前。挂起审批 Run ≥ batch_size（100）时，新 Run 的 `StartRun` 永远进不了窗口，**整个面新 Run 停摆，且各副本窗口相同、加副本无效**（审批 TTL 可达数小时，条件不苛刻）。
- 修法：投影落 `status`/`active_activity_count`/`wait_reason` 列，SQL 层只取真正可决策的行；或由投影器在活动结算/审批落定时置 `decision_due_at` 脏标记。

## P1（高概率真实触发的可靠性/安全缺陷）

### 运行时循环

- **P1-1 活动面无异常隔离 + `gather` 无 `return_exceptions`**（`activity_worker.py:148`、`execution_kernel_main.py:157-170`、`composition/tasks.py:141-142`）：一次 DB 抖动 → 活动面 lane 死亡 → CRITICAL 任务判定 → 整 kernel 进程退出；毒丸输入可形成 CrashLoop。控制面有 per-lane 隔离，活动面没有——补齐对称隔离 + 逐项收集异常。
- **P1-2 优雅关停不排水**（`composition/tasks.py:177-193` 直接 cancel 全部；`activity_worker.py:202-205` 非幂等活动恢复即 `NON_IDEMPOTENT_OUTCOME_UNKNOWN` 不可重试；ModelCall/ToolCall 均非幂等）：**每次滚动发布，所有正在跑模型/工具调用的 Run 一律永久 FAILED**。改为「停新 claim → 在 shutdown_timeout 内等在途 handler → 超时才 cancel」。
- **P1-3 Run 级重试零退避**（`run.py:327-345`、`decisions/base.py:71-73`）：失败即以决策循环节奏（约 1s）连打 max_retries 次，恰在下游 429/过载时放大流量。用现成 timer 机制调度指数退避的 `RetryRun`。
- **P1-4 inbox/outbox 已完成行无 GC**：每事件一条 outbox、每命令一条 inbox（带 ≤64KB payload），无任何 DELETE 路径，表与索引无限增长。加保留期分批清理或按月分区。

### 领域模型 / schema 演进

- **P1-5 upcast 管道只接通一半**（`sqlalchemy_orchestrator.py:309-329` 仅 orchestrator 升 public_payload；`postgres_formal_projector.py:373` 裸 evolve；`run.py:1015` 硬编码 schema 版本；internal_payload——含 RunPolicySnapshot——完全无演进通道）：第一次事件升版，projector 把该 Run 打入 poisoned 永久停摆，RunPolicySnapshot 演进则重放彻底炸。upcast 下沉到事件读取边界统一执行；internal_payload 建版本化模型。
- **P1-6 RunCreated v1→v2 用"重定基线"而非 upcaster**（`run.py:264,271`、`registry.py:47-48,71-72`）：库中存量 v1 事件重放抛 KeyError 逃逸捕获集（`sqlalchemy_orchestrator.py:125` 只捕 OSError/RuntimeError/ValueError）→ 无限毒命令循环，inbox 又无尝试上限/死信。立规矩：已上线事件禁止重定基线；KeyError 转毒流隔离；inbox 加 attempts 上限 + dead_letter。
- **P1-7 遥测事件塞进全序聚合流**（`run.py:71-83` state 八个只增元组累积全量 decision_data；`run.py:442-455` 每条 ActivityProgressed 走全序流；`postgres_event_store.py:229-237` append 全程持租户级 advisory 锁）：长 agent Run 重放 O(n²)、快照 MB 级膨胀，热租户所有 Run 的进度心跳串行过同一把锁。Progressed 移出聚合流（专用 append-only 表），activity_results 只留 ref+digest。

### 扩展面

- **P1-8 工具异常击穿为整 Run 失败**（`activities/tool_call.py:52` 无 try/except；`activity_worker.py:274-280` 任意异常 → ACTIVITY_HANDLER_ERROR；`decisions/base.py:118-124` → FailRun；`base.py:83-86` 只丢多余参数不校验必填）：模型一次参数幻觉/工具一次 ValueError = 杀死整个 Run，agent 无法自愈纠错。在 ToolCall handler 设异常边界，归一化为失败 tool result 喂回模型循环 + 按工具 schema 前置校验入参。
- **P1-9 definitions/invoke 无一致性（TOCTOU）+ 每步全量重建目录**（`agent_tool_catalog.py:89-135,160-309`）：model.call 与 tool.call 之间目录重建，MCP 掉线/skill 被禁用即"模型见过的工具执行时消失"→ 整 Run 失败；且每个 tool call O(全目录) 重算。目录快照随 model.call 落 decision_data，tool.call 按快照执行。
- **P1-10 双工具清单漂移，Vision 工具生产静默缺失**（`tool_registry.py:24-78` 死路径仅测试引用，却注册着 VisionTool/VisionGroundingTool；生产装配 `agent_tool_catalog.py:237-302` 没有它们；PatrolTool、`MCPTool.register_schema`、`CapabilityPolicy.for_child` 零引用）：删除/降级死清单为测试夹具，补上或显式删除 Vision，清死代码。
- **P1-11 MCP 信任边界**（`mcp_client.py:423-431` 远端 description/inputSchema 原样进 LLM prompt，`:497` structuredContent 原样透传；browser 有 UNTRUSTED 包裹而 MCP/A2A 没有）：纯 prompt 注入面。对 MCP 描述截断 + 同款 UNTRUSTED 包裹，限制 inputSchema 尺寸，返回内容统一包裹。
- **P1-12 Skill 白名单双重失配**（组标识 `"a2a"` vs 校验 `startswith("a2a_")` 各说各话，`tool_names.py:4` vs `skill_service.py:188-192`；导入的 SKILL.md `allowed_tools=[]` 被解释为"禁全部"而非"不限制"，`skill_import.py:34-41` + `capability_policy.py:90`）：导入技能挂上后所有工具静默不可用。统一常量、区分 `[]`/None 语义、frontmatter 换真 YAML 解析。

### 投影 / 观测

- **P1-13 单 scope 投影失败饿死其他 scope，且投影是写路径必经**（`execution_kernel.py:116-128` for 循环无 per-scope try/except；`postgres_formal_projector.py:370-371` hash mismatch 确定性 raise；决策源与活动上下文都消费该投影）：坏 scope 常驻队首 → 同批全部租户决策与执行停摆。per-scope 隔离 + 失败计数 + 复用 poisoned 模式 + 告警。
- **P1-14 内核 DB 指标从未刷新**（`execution_kernel.py:130-135` 的 `refresh_metrics` 全仓唯一调用方是契约测试）：inbox 深度/outbox lag/投影 lag Gauge 恒为 0，告警给出"一切正常"假象，恰好掩盖 P1-13。主循环/heartbeat 周期调用 + 行为级测试守住。
- **P1-15 投影落后对用户静默**（`run_control.py:69-96` SSE 0.2s 轮询、120s idle 无声 return）：lag 与"真没进展"不可区分。空转超时发显式事件；scope 级 lag 暴露到 status 端点。

## P2（结构性债务，按需排期）

- **P2-1 新增/升版事件要改 4-5 处且写侧不校验注册**（`run.py:230-250,268-273,1015` + evolve if 链）：聚合 `__init__` 自检 + append 前对照 registry。
- **P2-2 `extra="forbid"` + 严格 upcast = 零向前兼容**：滚动发布混合版本窗口是雷区。保留 fail-closed 可以，但须成文演进守则 + CI schema 快照测试。
- **P2-3 快照失效靠 hash 误报兜底**（`postgres_snapshot_store.py:110-117`）：schema 漂移与真损坏共用一个指标。分标签；RunState 变更必须 bump serializer_version 的 CI 守卫。
- **P2-4 canonical hash 与 pydantic model_dump 深耦合**（`serialization.py:19`、`store.py:135-153`）：pydantic 升级可致全库判"损坏"。golden-file 哈希回归测试；哈希输入显式构造。
- **P2-5 决策空转成本**：每秒每副本 ≥6 次查询 + 至多 100 个 RunState 全量解码哈希；wakeup xread 无 consumer group 全副本惊群；`execution_idle_poll_seconds` 名不副实（控制面节奏被硬编码 1000ms block 决定）。
- **P2-6 进度去重吞事件**（`activity_worker.py:375-418` dedupe 不含 claim_generation）：defer 重跑后进度静默停在上次位置。掺入 claim_generation。
- **P2-7 审批 TTL 注入致同 command_id 不同 payload**（`decision_worker.py:98-107` vs `base.py:270-273`）：多副本策略缓存不一时 inbox 抛 envelope 复用错，整批决策计零（可自愈但告警噪声）。TTL 移入聚合 decide 或排除出一致性比较。
- **P2-8 决策扩展点是 if/elif 链 + 魔法字符串**（`decisions/__init__.py:29-51`；activity 类型字符串决策侧/handler 侧重复书写）：常量收敛 + 启动期断言"决策使用集合 ⊆ 注册集合" + 改 dict 注册表。
- **P2-9 MCP 连接池全池单锁、无主动失效**（`connection_pool.py:89-103`；per-entry lock 已存在但未使用）：锁降到 fingerprint 级；连续失败条目 invalidate。
- **P2-10 skill 禁用语义分叉**（model_call 静默跳过 vs tool catalog 直接炸）：统一判定。
- **P2-11 能力判定散落 5 处**（装配筛选/暴露过滤/执行复核/审批推导/capability_service 独立实现 + search provider 字符串三方重复）：provider 枚举单点导出；审批推导挪进 ToolExecutionPolicy。
- **P2-12 取消/进度能力覆盖不均**：tool.call 被 cancel 时沙箱/浏览器长命令不终止；model.call 无进度通道；`context.heartbeat` 带而不用。
- **P2-13 组装泄漏进 infrastructure + import-linter 盲区**（`execution_ports.py:31,234-312` 反向 import 根模块与 6 个 application worker；契约不约束 infrastructure→composition；第 5 条契约开 `allow_indirect_imports` 豁免）：组装迁入 composition/；补反向契约。
- **P2-14 rebuild 非原子且误杀在途活动**（delete+commit 后再重放；窗口内 `RuntimePolicyIntegrityError` → 活动被判永久失败不 defer；且无任何运维入口调用 rebuild）：加 admin 入口；record 缺失返回可重试信号；重建中标记。
- **P2-15 审批通知非持久化**（投影 commit 后 best-effort dispatch，崩溃即丢，团队审批静默挂到 TTL 取消）：通知走 outbox 或加"已通知"标记补发 lane。
- **P2-16 双进程语义分叉**：①RLS 身份靠 contextvar 回退，同一投影实例在 API=用户身份 / kernel=系统身份（隐式 ambient authority）；②API 进程的 SandboxPool 无补货循环，误调 create() 静默退化冷启动。kernel 装配显式传系统身份；SandboxFactory 拆 attach-only/pooled 两窄接口。
- **P2-17 投影写放大 + 统计内存聚合**（逐事件数条往返；`approval_stats`/`governance_daily` 拉全表进内存）：按 run_id 聚批；统计改 SQL 聚合。
- **P2-18 长 handler 用陈旧批次 `now`；checkpoint FOR UPDATE 不 skip_locked**：outcome/defer 取当前时间；checkpoint 加 skip_locked。

## 建议级 / 死代码清理

- `api/app/kernel/` 确认死包（仅残留 __pycache__），整目录删除；`run.py:44-46` 过时注释修正。
- `application/execution/formal_projector.py` 的 FormalProjector 门面零调用方。
- admission.py 没有真正的准入闸门（无 per-tenant 活跃 Run 上限），海量小 Run 可放大 P0-1；建议加 per-scope 计数闸门形成显式背压边界。
- remediation/patrol 活动标 `idempotent=True` 使崩溃恢复直接重放执行——修复动作/巡检外呼是否真幂等需业务侧复核。
- 幂等/并发重试瑕疵：orchestrator 3 次冲突重试同事务内无退避。
- 观测补齐：inbox/projector/outbox/timer 四条 lane 无 OTEL span；`projection_hash_mismatch` 等指标枚举无调用位点；SSE 0.2s 轮询可事件化（chat 侧已有 wakeup 先例）。
- 游标密钥用 `sha256(api_key_secret)` 派生，轮换密钥会打断在途 SSE 游标——独立密钥 + 双密钥解码。
- 扩展点范式收敛：以 `inference/registry.py` 的 ProviderSpec 声明式注册为范本，统一工具/决策/搜索四种各异的注册模式。

## 附：新增一个原生工具的实测改动清单

最少 2 处、典型 4-5 处、无单点注册：①新工具文件（继承 BaseTool + @tool）；②capability_policy.py（如需新策略）；③agent_tool_catalog.py `_build()` 加 candidates（有新依赖还要改 __init__ 签名）；④composition/kernel.py + shared.py 注入依赖；⑤tool_registry.py（不改则与死路径继续漂移）；⑥skill_service.py 各内置 skill 白名单；⑦capability_service.py UI 面板条目。对比：新增 LLM provider 只需 2 处（最优范式）。

## 建议的修复分包（供选择）

- **K-A 可用性止血包（P0-1, P1-1, P1-2, P1-3, P1-8）**：决策源就绪过滤、活动面异常隔离、关停排水、重试退避、工具异常喂回模型。全部可在现有抽象内局部修复，直接决定 agent 实际可用性。
- **K-B schema 演进收口包（P1-5, P1-6, P2-1~P2-4）**：统一 upcast 管道、internal_payload 版本化、演进守则 + CI 守卫（golden hash / serializer_version / 事件注册自检）。在第一次真实事件升版之前必须完成。
- **K-C 观测与投影韧性包（P1-13, P1-14, P1-15, P2-14, P2-15）**：per-scope 投影隔离、指标接线、SSE lag 显式化、rebuild 运维入口、审批通知持久化。
- **K-D 扩展面统一包（P1-9~P1-12, P2-8~P2-12, 死代码清理）**：目录快照一致性、删除双清单、MCP 信任边界、skill 白名单语义、声明式注册收敛。
- **K-E 性能与容量包（P1-4, P1-7, P2-5, P2-17）**：队列 GC、遥测事件出流、决策脏标记/惊群治理、投影聚批。
