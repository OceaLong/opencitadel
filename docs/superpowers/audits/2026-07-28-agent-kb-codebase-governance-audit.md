# Agent、文档知识库与代码知识库实现审计

> **状态:已过时(superseded)。** 本审计基于 2026-07-28 的代码快照。其中 P0-05/07/08/09
> 所指的资源版本化/原子发布/版本绑定,已由 commit db986f2(2026-07-31)落地实现
> (见 `api/app/application/services/*_version_service.py` 与迁移 e9f0a1b2c3d4)。
> 保留本文作为审计轨迹,勿据此判断当前实现状态。
> 注:文中引用的审计基线 spec 按本项目惯例不入库,存于本地。
>
> 2026-08-04 复核：全部 P0 已闭环，见 2026-08-04-governance-p0-reverification.md

- 日期：2026-07-28
- 审计基线：`docs/superpowers/specs/2026-07-28-agent-kb-codebase-governance-design.md`
- 审计对象：当前 `main` 分支实现
- 结论：**不满足 Spec 的 P0 上线门槛**

## 1. 审计方法

本次审计沿三条功能主线检查：

1. API/服务层的模式、权限和资源就绪判断。
2. Agent Flow、工具装配、审批、并发、重试和任务终态。
3. 文档与代码摄取的状态机、失败原子性、降级、恢复和事件。
4. 检索、图谱、静态分析、源码读取和证据链。
5. UI 是否忠实表达后端状态和产品能力。
6. 现有单元测试是否覆盖 Spec 的全局不变量。

严重度：

- **P0**：可能造成未授权副作用、重复副作用、错误终态、数据/索引不可用、越权或不可复现，必须先修。
- **P1**：核心能力可用性、正确性或证据可信度不足，应在第二阶段完成。
- **P2**：性能、交互和可维护性问题，可在核心契约稳定后优化。

## 2. 结论摘要

| 严重度 | 数量 | 主要主题 |
|--------|------|----------|
| P0 | 12 | Ask 越权、审批竞态、重复副作用、双终态、无版本绑定、非原子构建、安全与 RBAC |
| P1 | 8 | 降级检索、图谱真实性、代码证据、恢复、事件重放 |
| P2 | 3 | 摄取性能、GraphRAG 成本、重复 Flow/大类维护 |

现有实现有若干良好基础：

- 知识库已实现文档级增量摄取、BM25/向量混合检索和实体来源引用。
- 知识库新增 URL 已调用 public URL 校验。
- 会话、知识库和代码库多数读取路径已传递 workspace scope。
- Agent 已有计划审批、调用级审批、检查点和任务心跳。
- 代码库已保存文件 digest、符号位置和部分调用边。

但这些能力尚未形成端到端不变量。当前最危险的问题不是“缺少功能”，而是 UI/文档承诺的边界与运行时实际行为不一致。

## 3. P0 发现

### P0-01 Ask 模式可通过 extra tools、MCP/A2A 和子 Agent 产生副作用

**违反：** Ask 零副作用、统一授权。

**证据：**

- `ToolRegistry.build_ask_tools()` 无条件加入 MCP 和 A2A，并把所有 `extra_tools` 原样加入：`api/app/domain/services/tools/tool_registry.py:56-70`。
- `TaskRunnerFactory` 在判断会话模式前就加入 Memory、Artifact、ImageGeneration，并最终加入 SubAgent：`api/app/application/services/task_runner_factory.py:331-379`、`426-436`、`470-503`。
- Ask Flow 接收完整 `extra_tools`：`api/app/domain/services/flows/doc_qa_flow.py:54-62`、`code_ask_flow.py:54-64`、`hybrid_ask_flow.py:54-62`。
- SubAgent 使用 `build_default_tools()`，默认含 File、Shell、Browser；没有 Skill 白名单时 `parent_allowed` 为 `None`，子 Agent 不受限：`api/app/domain/services/subagent_factory.py:84-105`。

**故障机制：**

Ask 主 Agent 可以直接调用 Artifact、Memory、ImageGeneration 或未声明只读的集成工具，也可以调用子 Agent，再由子 Agent 执行 Shell、文件写入或浏览器动作。这是跨一层调用的权限提升。

**要求：**

- 为所有工具声明 effect/idempotency。
- Ask 构建器仅从只读 allowlist 选择工具。
- MCP/A2A 默认不可进入 Ask，只有管理员确认只读声明后允许。
- 子 Agent 显式继承父 CapabilityPolicy，只能收窄。
- 删除“先构建所有 extra tools，再交给 Flow 自己筛选”的装配方式。

**验收测试：**

对 Ask 主 Agent、Skill、SubAgent、MCP/A2A 分别尝试文件写入、Artifact、浏览器和外部写入，全部在工具执行前被拒绝，副作用计数为零。

### P0-02 多工具调用会在审批完成前执行同批次副作用，并覆盖待审批状态

**违反：** 审批前零执行、单一审批队列。

**证据：**

- 每个 tool call 在自己的协程内部检查 gate，所有调用随后用 `asyncio.gather()` 同时运行：`api/app/domain/services/agents/base.py:1134-1197`。
- 任一调用进入 waiting 后，其他协程可能已经执行 `_invoke_tool()`；其结果消息在 wait 分支被丢弃：`base.py:1151-1189`、`1197-1206`。
- 每个待审批调用写同一个 `pending_tool_call` 字段：`base.py:1456-1474`。
- 恢复路由也只读取一个 `pending_tool_call`：`api/app/interfaces/endpoints/session_routes.py:100-133`。

**故障机制：**

一个模型响应同时包含“需审批的浏览器动作”和“不需审批的外部写工具”时，外部写工具可先执行。多个需审批调用并发写相同 metadata 时，最后一次写入覆盖其他调用，产生审批丢失或批准对象错位。

**要求：**

- 对整个 tool-call batch 先做纯计算预检。
- 任何副作用执行前先持久化完整 approval queue。
- 待审批时只读结果可保留，有副作用调用一律不执行。
- Resume 按稳定 tool_call_id 幂等消费。

### P0-03 所有工具统一自动重试，可能重复非幂等副作用

**违反：** 非幂等不自动重试。

**证据：**

`BaseAgent._invoke_tool()` 对 MCP/A2A 返回失败、超时和任意异常均按 `agent_config.max_retries` 重试：`api/app/domain/services/agents/base.py:727-777`。

**故障机制：**

浏览器点击、Shell、文件写入、外部工单/消息/付款等调用可能已经成功但响应超时。框架立即重试会重复产生副作用。当前没有 effect、idempotency key 或 `outcome_unknown` 状态参与决策。

**要求：**

- 重试由 ToolExecutionPolicy 决定。
- 非幂等和未知工具不自动重试。
- `idempotent_with_key` 工具必须先生成稳定 key。
- 外部写超时进入 `outcome_unknown`，先查状态或请求用户确认。

### P0-04 ErrorEvent 不决定失败，任务仍可被标记 completed

**违反：** 单一终态。

**证据：**

- CodeAsk/HybridAsk 捕获异常后发出 ErrorEvent，却把 Flow 设为 COMPLETED：`api/app/domain/services/flows/code_ask_flow.py:81-102`、`hybrid_ask_flow.py:79-100`。
- DocQA 还会吞掉 IntegrityError 并直接 Done：`doc_qa_flow.py:79-111`。
- `AgentTaskRunner` 只透传 ErrorEvent，没有记录失败 outcome：`api/app/domain/services/agent_task_runner.py:380-399`。
- 主循环只对 WaitEvent 提前返回，正常退出后一律写 `SessionStatus.COMPLETED`：`agent_task_runner.py:546-585`、`598-608`。

**故障机制：**

客户端可以先收到 error，再收到 completed；UI 的本地 failed 状态会被 completed 覆盖。自动化、通知和审计回调会把失败任务当成功处理。

**要求：**

- Flow 返回显式 RunOutcome。
- Worker 仅依据 RunOutcome 写终态。
- 服务端事件状态机拒绝 failed→completed 等非法序列。
- Token 记账失败与主业务失败分层处理，不能静默吞掉整轮结果。

### P0-05 会话只绑定逻辑资源 ID，历史回答不可复现

**违反：** 历史可复现、发布原子性。

**证据：**

- Session 只有 `codebase_id`、`knowledge_base_id`，没有版本 binding：`api/app/domain/models/session.py:40-49`、`api/app/infrastructure/models/session.py:73-82`。
- KnowledgeBase/Codebase 只有单份状态和索引指针，没有 active version：`api/app/infrastructure/models/knowledge_base.py:25-52`、`codebase.py:27-64`。
- 全仓未发现 `resource_version`、`active_version` 或 `session_resource_binding` 实现。

**故障机制：**

同一会话在资源重建前后会读取不同数据；删除文档、重新分析或覆盖快照会改变历史引用的真实含义。无法实现已确认的“旧会话固定版本、主动升级”。

**要求：**

先落地版本表、version-scoped 数据、active pointer 和 session binding，再迁移重建流程。所有检索和原文/源码读取必须显式接收 version_id。

### P0-06 文档在索引成功前被标记 READY

**违反：** 发布原子性、降级可见。

**证据：**

- 文档解析成功后立即更新为 READY：`api/app/domain/services/knowledge_base/ingestion_runner.py:87-98`。
- 分块和索引在之后执行，索引异常会直接把 KB 设为 FAILED：`ingestion_runner.py:123-181`。
- KB 的 ready_doc_count 直接统计 READY 文档并作为会话门槛：`api/app/application/services/knowledge_base_service.py:163-178`、`251-275`。
- `_finalize_kb()` 只要存在 READY 文档就把 KB 设为 READY：`ingestion_runner.py:244-264`。

**故障机制：**

新文档解析成功但分块或索引失败时仍可能被 ready_doc_count 计入。用户可创建 Ask 会话，但文档没有可检索 chunks，或命中旧残留。

**要求：**

把文档 revision 状态拆为 parsed/indexed；只有候选版本核心索引校验并发布后，manifest 才对会话可见。

### P0-07 知识库全量 reindex 先清除在线索引

**违反：** 发布原子性、旧版本持续可用。

**证据：**

`KnowledgeBaseService.reindex()` 在派发 Worker 前把所有文档标记 pending 并直接 `clear_index_data()`：`api/app/application/services/knowledge_base_service.py:213-221`。

**故障机制：**

从清空到构建完成存在检索空窗；后续任何解析、Worker、Embedding 或数据库故障都会让原本可用的知识库失去索引。

**要求：**

reindex 只创建候选 version/build，旧 active version 不变；候选通过核心校验后原子切换。

### P0-08 代码库 reanalyze 可并发运行并直接清空在线分析数据

**违反：** 发布原子性、构建幂等。

**证据：**

- `CodebaseService` 已有 `_ingest_in_progress()`，但 `reanalyze()` 未调用它，每次都创建并派发新任务：`api/app/application/services/codebase_service.py:56-68`、`190-206`。
- Runner 在分析开始时直接 `clear_analysis_data()`：`api/app/domain/services/codebase/ingestion_runner.py:63-82`。
- 清空和后续 files/symbols/edges/chunks/artifacts 分布在多个 UoW：`ingestion_runner.py:69-139`。
- UI 在 ingesting 时仍启用 reanalyze：`ui/src/components/codebase/codebase-library.tsx:194-247`。

**故障机制：**

两个构建可交叉执行 clear/save，最终发布混合代次数据。单次构建失败也会留下空或部分索引。

**要求：**

同一资源单 active build；重复请求幂等返回现有 build。所有数据写候选 version namespace，发布事务只切 active pointer。

### P0-09 代码库源码快照与工作区未随重建失效，可能读取旧代码或残留文件

**违反：** 历史可复现、证据一致性。

**证据：**

- reanalyze 不清除或重建 `snapshot_key`：`api/app/application/services/codebase_service.py:190-206`。
- snapshot 只在下载或 Agent attach 时按需生成：`codebase_service.py:281-325`。
- attach sentinel 只含 codebase_id，无版本或 digest；存在即跳过 restore：`codebase_service.py:303-337`。
- ZIP/FILES 物化只 `mkdir -p` 并覆盖上传，不清理旧工作区；只有 Git 路径显式 `rm -rf`：`api/app/domain/services/codebase/ingestion_runner.py:170-225`。

**故障机制：**

删除的文件仍残留在重建工作区；新分析完成后 Agent 可能恢复旧 snapshot；同一会话永远跳过新版本 attach。索引、源码读取和实际工作区可能分别代表三个不同版本。

**要求：**

每个 build 使用干净工作区并生成不可变 snapshot/digest；sentinel 绑定 version_id；会话升级显式处理本地修改冲突。

### P0-10 Git 来源和源码读取存在命令注入、SSRF 与路径越界风险

**违反：** 统一授权、源码与文件安全。

**证据：**

- Git URL 原样进入 shell f-string：`api/app/domain/services/codebase/ingestion_runner.py:182-189`。
- 创建服务不校验 Git URL、ZIP file_id、FILES file_ids 的必填和所有权：`api/app/application/services/codebase_service.py:79-105`。
- API schema 允许所有来源字段为空：`api/app/interfaces/schemas/codebase.py:19-24`。
- `read_source()` 和 `CodebaseTool.read_code()` 仅拼接 `path.lstrip('/')`，没有 canonical containment 检查：`codebase_service.py:261-279`、`api/app/domain/services/tools/codebase_tools.py:181-202`。

**故障机制：**

恶意 Git URL 可注入 shell 参数或访问内网；`../` 路径可尝试逃离代码库根目录；空 FILES 来源可进入摄取并被错误标记就绪。

**要求：**

使用参数化 clone/SDK；验证协议、主机、重定向和解析后 IP；服务层校验来源所有权；ZIP 安全解压；所有读取使用 canonical path containment。

### P0-11 写路由的 Auditor guard 和资源就绪校验不一致

**违反：** 统一授权。

**证据：**

- Knowledge `add_documents`、`reindex`、`create session` 没有 `require_non_auditor`：`api/app/interfaces/endpoints/knowledge_base_routes.py:80-94`、`140-168`。
- Codebase `reanalyze`、`create session` 和会产生 snapshot/DB 写入的 GET download 没有写 guard：`api/app/interfaces/endpoints/codebase_routes.py:162-202`、`172-180`。
- 通用 SessionService 只校验资源存在，不校验 READY/发布状态：`api/app/application/services/session_service.py:53-103`。
- Codebase 专用创建会话同样只校验存在：`api/app/application/services/codebase_service.py:239-259`。
- Knowledge 专用创建会话虽检查 ready_doc_count，却强制把 Agent 改回 Ask：`knowledge_base_service.py:251-275`。
- UI 资源列表和详情跳转直接调用通用 session API，绕过领域服务门槛：`ui/src/components/knowledge/knowledge-library.tsx:225-235`、`knowledge-detail-redirect.tsx:15-27`、`codebase/codebase-library.tsx:147-157`。

**故障机制：**

同一动作通过不同路由得到不同权限和就绪结果。Auditor 可调用部分变更接口；未就绪资源可通过通用会话入口创建；产品确认的 KB Agent 无法启动。

**要求：**

建立共享 ResourceGuard/SessionBindingService；所有入口统一验证角色、scope、发布版本和模式。KB Agent 不再强制降级。

### P0-12 Worker lease 冲突消息不 ack，且 Codebase 没有 stuck-build 对账

**违反：** 单一终态、构建恢复。

**证据：**

- lease 未获取时直接 return，`ack_dispatch()` 只在实际执行成功后调用：`api/app/worker/main.py:373-406`。
- Worker 只有 Agent orphan reconcile 和 KB stuck ingest reconcile：`worker/main.py:249-371`。
- Codebase ingest 只有执行入口，没有对应 stuck reconcile：`worker/main.py:609-624`。

**故障机制：**

lease 冲突的 pending Redis message 会被反复 autoclaim；原执行释放 lease 后，旧消息可能再次触发同一任务。Codebase Worker 崩溃后资源可永久停留在中间状态。

**要求：**

为 duplicate/lease-conflict 定义明确 ack 或安全 requeue 语义；引入 `task_id + run_generation`；三类任务统一进入 heartbeat/stuck-run 对账。

## 4. P1 发现

### P1-01 Codebase 向量降级后没有关键词检索回退

**证据：**

- `semantic_search()` 必须先生成 query embedding，只调用 pgvector search：`api/app/domain/services/tools/codebase_tools.py:34-52`。
- repository 在 embedding 为空时直接返回空：`api/app/infrastructure/repositories/db_codebase_repository.py:237-274`。
- `DBCodebaseRepository.save()` 更新已有记录时遗漏 `record.vector_degraded = codebase.vector_degraded`：`db_codebase_repository.py:41-61`。

**影响：**

Embedding 故障会被展示成“未找到相关代码”，且降级标记可能不持久化。与已确认的核心关键词索引门槛不符。

**要求：**

新增路径/符号/源码全文索引并与向量候选融合；query embedding 失败时自动使用关键词；修复 degraded 字段持久化。

### P1-02 代码库预生成架构图、数据流和流程图包含虚构关系

**证据：**

- architecture 固定输出 UI→API→Domain→Infra：`api/app/domain/services/codebase/artifact_generator.py:93-139`。
- data flow 固定输出 User→UI→API→Service→DB/Sandbox：`artifact_generator.py:141-158`。
- flowchart 将前 15 个函数按列表顺序串联：`artifact_generator.py:235-253`。

**影响：**

产物看起来是分析结果，实际是模板或排列顺序，违反“证据不足不生成”。

**要求：**

Artifact 只消费已验证依赖/调用/数据流边；每条事实携带 EvidenceRef、analyzer 和 confidence；无证据返回 unsupported。

### P1-03 静态分析会错误合并或错误解析符号

**证据：**

- 非 Python 分析按文件内名称去重，同名方法只保留一个，且 `end_line=start_line`：`api/app/domain/services/codebase/static_analyzer.py:201-241`。
- 调用解析用全局名称索引并任取第一个同名符号：`static_analyzer.py:243-280`。
- Python parent 查找同样按类短名取第一个：`static_analyzer.py:172-195`。

**影响：**

语义 chunk 可能只有签名；调用链边指向错误同名函数；产物没有歧义或置信度标记。

**要求：**

符号使用限定名和作用域；无法唯一解析的边标记 ambiguous；逐语言引入 AST/tree-sitter，正则结果明确低置信度。

### P1-04 知识图谱 UI 不展示 GraphRAG 数据

**证据：**

`KnowledgeContextPanel.graphForDocs()` 只把最多 20 个文档标题渲染成无边节点：`ui/src/components/workspace/knowledge-context-panel.tsx:15-20`、`63-66`。

**影响：**

用户看到的“图谱”与后端 entities/relations 无关，既无关系也无来源证据。

**要求：**

新增版本化 graph API，UI 读取真实实体、关系和引用；能力不可用时展示状态，不渲染假图。

### P1-05 Graph 查询可能显示内部 UUID，实体写入存在竞态

**证据：**

- graph_search 只加载命中的实体，关系另一端不在 map 时直接输出 ID：`api/app/domain/services/tools/knowledge_base_tools.py:85-105`。
- entity upsert 先 select、再逐项 add，没有数据库唯一冲突处理：`api/app/infrastructure/repositories/db_knowledge_base_repository.py:360-391`。

**影响：**

关系输出不可读；并发 Graph build 可插入重复实体。

**要求：**

服务端补齐关系端点；数据库增加版本级 normalized identity 唯一约束并使用原子 upsert。

### P1-06 文档错误/警告和原文预览状态可能过期或截断

**证据：**

- `update_document_status()` 仅在 error/warning 非 None 时更新，成功重试不会清除旧值：`api/app/infrastructure/repositories/db_knowledge_base_repository.py:188-205`。
- read_document 默认只读取 30 个 chunks 且不返回 cursor/total：`api/app/application/services/knowledge_base_service.py:277-296`。
- Agent 工具同样固定 30 个 chunks：`api/app/domain/services/tools/knowledge_base_tools.py:107-130`。
- UI 无页码时直接加载该截断结果：`ui/src/components/workspace/knowledge-context-panel.tsx:68-80`、`103-110`。

**影响：**

成功文档仍显示旧失败；长文档尾部无法访问，且用户不知道内容被截断。

**要求：**

状态更新支持显式 clear；读取 API 返回游标、总量和截断信息；UI 提供分页/继续加载。

### P1-07 摄取 SSE 依赖 Redis 输出流，没有持久化构建事件

**证据：**

- KB/Codebase stream 都直接读取 `task:output:{task_id}`：`api/app/application/services/knowledge_base_service.py:223-249`、`codebase_service.py:208-237`。
- UI 同时用 SSE 和 5 秒轮询修补状态：`ui/src/components/knowledge/knowledge-library.tsx:208-223`。

**影响：**

流裁剪、Worker 重启或断线后无法完整重放构建历史，客户端需用轮询猜测最终状态。

**要求：**

构建事件写持久化 event log，Redis 只做实时分发；SSE cursor 支持重放。

### P1-08 Codebase Ask 依赖长期存活的摄取沙箱，Agent attach 失败被静默忽略

**证据：**

- Ask 优先读取 `codebase.sandbox_id` 对应摄取沙箱：`api/app/application/services/task_runner_factory.py:381-403`。
- 源码读取在 sandbox 不存在时直接失败：`api/app/application/services/codebase_service.py:261-279`。
- Agent attach callback 捕获所有异常仅记录 warning，仍继续创建 Runner：`task_runner_factory.py:290-315`。

**影响：**

摄取沙箱被清理后 Ask 无法读源码；Agent 可能在空工作区继续执行并给出错误结论。

**要求：**

每个发布版本必须有 snapshot；Ask/Agent 从 snapshot 按需恢复。关键上下文 attach 失败必须让 Run 明确失败或降级，不能静默继续。

## 5. P2 发现

### P2-01 文件采集先截断再过滤，并逐文件远程读取

**证据：**

- 命令先执行 `find ... | head -n 5000`，之后 Python 才过滤 node_modules 等目录：`api/app/domain/services/codebase/ingestion_runner.py:227-252`。
- 每个文件顺序调用 sandbox.read_file：`ingestion_runner.py:247-263`。
- 空 entries 没有发布门槛，后续仍可生成 artifacts 并 READY：`ingestion_runner.py:63-139`。

**影响：**

大型 node_modules 可耗尽 5000 配额，真实源码被饿死；远程往返慢；空分析也可能成功。

**要求：**

遍历时过滤和计量；在沙箱内批量分析或批量读取；无可分析源码时构建失败；暴露截断范围。

### P2-02 GraphRAG 只有单文档上限，没有全构建预算

**证据：**

`GraphBuilder` 每个文档最多取 200 个父块，但把所有文档选中项一次性建立 gather 任务：`api/app/domain/services/knowledge_base/graph_builder.py:49-75`。

**影响：**

大量文档可产生高额 LLM 调用和内存占用；失败只按 chunk 日志记录，缺少结构化 partial 状态。

**要求：**

增加构建级调用/Token/时间预算和有界 producer-consumer 队列；超限标记 graph_partial 并可续建。

### P2-03 模式路由与 Ask Flow 重复，维护时容易再次漂移

**证据：**

- KB-only Agent→Ask 规则分别出现在 SessionService 和 AgentService：`api/app/application/services/session_service.py:69-76`、`agent_service.py:267-293`。
- AgentTaskRunner 再次根据资源组合决定 Flow：`api/app/domain/services/agent_task_runner.py:151-228`。
- DocQA、CodeAsk、HybridAsk 的构造和异常处理高度相似，但行为已不一致：三个 `api/app/domain/services/flows/*_ask_flow.py`。
- `BaseAgent`、`AgentTaskRunner`、`TaskRunnerFactory` 同时承担装配、策略和执行细节。

**影响：**

新增模式、资源组合或异常策略时需要修改多个位置，当前 KB Agent 强制降级和 DocQA 特殊吞异常就是漂移结果。

**要求：**

用声明式 SessionFlowResolver + CapabilityPolicy 决定 Flow 和工具；抽取统一 AskFlow 模板，仅保留 retriever/prompt 差异。

## 6. 测试覆盖审计

当前测试能证明已有实现内部行为稳定，但没有证明 Spec 不变量成立。

本次审计实际运行：

- Agent/Ask/工具装配相关：27 passed。
- 文档知识库 Runner、Retriever、Service、Repository：49 passed。
- 代码库 Runner、Analyzer、Artifacts、Tools、删除服务：16 passed。
- UI 知识库工具与 session event：20 passed。

全部选定基线测试通过，说明下述差距不是已有测试回归，而是当前契约本身及测试边界没有覆盖 Spec 要求。

已有覆盖：

- Ask Flow 使用 `build_ask_tools`。
- KB 增量摄取、实体引用清理和向量降级。
- Codebase 摄取、静态分析、Artifacts 和工具基本行为。
- HITL 单调用恢复和部分任务终态行为。

关键缺口：

1. Ask 通过 SubAgent/MCP/A2A/extra tool 越权的负向测试。
2. 同一 LLM 响应包含多个副作用调用与多个审批调用的竞态测试。
3. 非幂等工具超时/异常不重试测试。
4. ErrorEvent 与 SessionStatus 单终态性质测试。
5. 任意摄取阶段故障后 active version 不变的故障注入测试。
6. 并发 reindex/reanalyze 幂等测试。
7. 旧会话固定版本、新会话使用新版、升级只影响后续消息的测试。
8. Codebase embedding 故障下关键词回退测试。
9. 图和代码产物的每条边具备 EvidenceRef 测试。
10. Git/ZIP/path traversal、Auditor 替代路由和未就绪会话创建测试。
11. Worker lease conflict、autoclaim 和 Codebase stuck-build 对账测试。

## 7. Spec 符合度矩阵

| Spec 不变量 | 当前状态 | 主要发现 |
|-------------|----------|----------|
| Ask 零副作用 | 不符合 | P0-01 |
| 审批前零执行 | 不符合 | P0-02 |
| 非幂等不自动重试 | 不符合 | P0-03 |
| 单一终态 | 不符合 | P0-04、P0-12 |
| 发布原子性 | 不符合 | P0-06、P0-07、P0-08 |
| 历史可复现 | 不符合 | P0-05、P0-09 |
| 降级可见 | 部分符合 | P1-01、P1-06 |
| 证据优先 | 不符合 | P1-02、P1-03、P1-04、P1-05 |
| 统一授权 | 不符合 | P0-10、P0-11 |

## 8. 修复顺序建议

### Gate 1：先封住副作用与终态

完成 P0-01 至 P0-04。未完成前，不应扩展 Ask 集成或增加 Agent 自动重试。

### Gate 2：建立版本与原子发布骨架

完成 P0-05 至 P0-09，并先迁移现有资源为 v1。后续 KB/Codebase 优化都基于 version_id，避免重复改造。

### Gate 3：统一安全和恢复

完成 P0-10 至 P0-12。所有路由切到共享 guard，三类 Worker 任务进入同一恢复模型。

### Gate 4：提升检索与证据

完成 P1-01 至 P1-08。Codebase 先有关键词核心检索，再改静态分析和图表；KB 先接真实 graph API，再替换 UI。

### Gate 5：性能与维护性

完成 P2 项，最后拆分大类与重复 Flow，避免在核心协议仍变化时做无收益重构。

## 9. 审计结论

当前实现不能通过 Spec 的 P0 验收。建议实施计划以四个强依赖顺序推进：

1. Agent 治理契约。
2. 资源版本、构建记录和迁移骨架。
3. KB/Codebase 版本化管线与统一安全恢复。
4. 检索、证据、UI 和性能。

不得先单独修 UI 状态、假图或单个 READY bug 后宣称完成，因为缺少版本 namespace 时，重建失败和历史漂移仍然存在。
