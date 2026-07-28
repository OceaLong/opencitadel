# Agent、文档知识库与代码知识库治理设计

- 日期：2026-07-28
- 状态：已确认，待实施
- 范围：Agent 对话与受治理自主执行、文档知识库、代码知识库

## 1. 背景

OpenCitadel 已具备 Agent/Ask 会话、文档知识库摄取与检索、代码库静态分析与语义检索三条主线，但当前实现中的边界和状态语义并不一致：

- Ask 的产品语义是只读问答，工具装配却可能通过通用工具、子 Agent 或集成工具产生副作用。
- Agent 工具并发、审批和重试分别实现，无法保证“先审批、后执行、且只执行一次”。
- Flow 发出的错误事件与任务最终状态没有统一契约，可能同时出现错误和完成。
- 文档和代码索引会在构建过程中直接修改当前数据，失败时可能暴露空索引、部分索引或错误的 READY 状态。
- 会话只绑定资源 ID，没有绑定不可变资源版本，重建后难以复现历史回答。
- 文档知识库与代码知识库的降级检索、状态展示、恢复和安全校验不一致。
- 部分知识图谱与代码图表不能完整追溯到真实实体关系或源码证据。

本设计以三个共享机制解决这些问题：

1. **能力边界**：模式、工具副作用、审批、重试和子 Agent 继承同一策略。
2. **不可变资源版本**：构建在暂存版本中完成，校验后原子发布，会话固定绑定版本。
3. **显式终态协议**：Flow、任务、资源构建与 UI 对成功、失败、等待、取消和降级使用一致语义。

## 2. 已确认的产品决策

1. 文档知识库和代码知识库都提供 Ask 与 Agent 两种入口。
2. Ask 是严格只读模式，只允许知识/代码检索、原文/源码读取，以及明确声明为只读的集成工具。
3. 文件写入、Shell、浏览器执行、交付物写入及任何外部状态变更只能在 Agent 模式发生。
4. 会话创建时固定知识库、代码库的具体版本；新会话默认使用当前发布版本。
5. 旧会话仅在用户主动执行“升级上下文”后切换版本，升级只影响后续消息，历史消息保留原版本标记。
6. 解析与关键词索引是发布的必需条件；向量失败时允许降级发布；知识图谱和代码分析图失败时允许核心检索继续服务。
7. 重建失败不能影响当前发布版本。
8. 图谱、架构图、数据流和调用链必须有真实数据或源码证据；证据不足时不生成。
9. 接口和数据库采用增量迁移，现有客户端至少保留一个兼容周期。
10. 总设计先统一约束，实施按三条主线和共享基础设施拆分。

## 3. 目标与非目标

### 3.1 目标

- 保证 Ask 模式不会直接或间接产生副作用。
- 保证 Agent 中需审批的操作在审批前零副作用，非幂等操作不会被框架自动重复执行。
- 保证每个任务恰好产生一个可信终态。
- 保证资源重建期间旧版本持续可用，新版本只在通过校验后可见。
- 保证历史回答可定位到资源版本和证据。
- 保证向量服务不可用时，文档和代码仍具备可解释的关键词检索能力。
- 保证图谱与代码分析产物不虚构关系。
- 统一权限、所有权、路径、来源 URL 和写接口安全约束。
- 为现有数据和 API 提供可回滚、可观测的迁移路径。

### 3.2 非目标

- 不在本轮更换 LLM、向量数据库、消息队列或沙箱供应商。
- 不把文档和代码摄取强行合并成同一套领域流水线。
- 不保证 Agent 对外部系统业务动作具备 exactly-once 语义；系统保证的是审批、调度和重试边界可控，并通过幂等键尽可能实现去重。
- 不在本轮实现跨会话自动追踪“最新资源版本”；升级必须由用户显式触发。
- 不把 Agent 会话中的代码修改自动回写为代码库的新发布版本。

## 4. 方案比较

### 4.1 方案 A：局部修补

直接修复 READY 时序、Ask 工具列表、重试条件、代码库关键词回退等已知问题。

优点是改动小、交付快；缺点是模式判断、审批、构建状态和资源绑定仍分散在多个服务中，后续新增 Flow 或资源类型会再次产生语义漂移。

### 4.2 方案 B：共享治理协议 + 领域版本模型（采用）

保留 Agent、文档知识库、代码知识库各自的实现与数据特征，引入四个薄的共享契约：

- `CapabilityPolicy`：决定模式允许哪些能力。
- `ToolExecutionPolicy`：描述副作用、审批、幂等和并发规则。
- `ResourceVersionBinding`：把会话绑定到不可变资源版本。
- `RunOutcome` / `BuildOutcome`：统一任务和构建终态。

知识库与代码库分别拥有自己的版本表、索引表和构建 Runner，共享“暂存—校验—原子发布”的协议。

该方案能解决根因，同时避免一次性重写摄取平台，是本设计的采用方案。

### 4.3 方案 C：统一资源摄取平台

将文档、代码和未来数据源全部抽象为统一 DAG、统一版本存储和统一检索接口。

长期一致性最高，但会同时改动 Worker、领域模型、索引、任务恢复和 API，迁移风险与验证成本过高，不适合作为当前优化的第一步。

## 5. 全局不变量

以下不变量是实现和验收的最高优先级：

1. **Ask 零副作用**：Ask 的主 Agent、子 Agent、MCP/A2A 和未来扩展工具都不得突破只读能力集。
2. **审批前零执行**：一个批次中只要存在待审批调用，任何有副作用调用都不能提前执行。
3. **非幂等不自动重试**：框架不得自动重试未知或非幂等副作用。
4. **单一终态**：同一 Run 只能是 `succeeded`、`failed`、`cancelled` 或 `waiting` 之一；终态不可被后续事件覆盖。
5. **发布原子性**：未通过校验的资源版本不能成为 active version。
6. **历史可复现**：每条基于资源生成的回答都能解析出实际版本和证据。
7. **降级可见**：任何能力降级都必须结构化记录，并在 API、事件和 UI 中可见。
8. **证据优先**：无法关联证据的图节点、边或结论不得作为事实展示。
9. **统一授权**：同一资源的所有入口使用同一授权和就绪检查，不因路由不同而改变。

## 6. 领域与数据模型

### 6.1 会话资源绑定

新增 `session_resource_bindings`：

| 字段 | 说明 |
|------|------|
| `id` | 绑定 ID |
| `session_id` | 会话 ID |
| `resource_kind` | `knowledge_base` 或 `codebase` |
| `resource_id` | 逻辑资源 ID |
| `version_id` | 不可变版本 ID |
| `bound_at` | 首次绑定或升级时间 |
| `bound_by` | 操作用户 |
| `supersedes_binding_id` | 升级前的绑定，可空 |

约束：

- 同一会话、资源类型在任一时刻只有一个当前绑定。
- 创建会话时若请求未传 `version_id`，服务端在事务中解析并固化 `active_version_id`。
- 不允许绑定 `building`、`failed` 或未发布版本。
- 消息记录保存本轮使用的 binding 快照；升级不改写历史消息。
- 兼容期内保留 `sessions.knowledge_base_id`、`sessions.codebase_id`，但只作为逻辑资源引用，读取上下文以 binding 为准。

### 6.2 知识库版本

新增：

- `knowledge_base_versions`
  - `id`, `knowledge_base_id`, `parent_version_id`
  - `state`, `build_id`, `created_at`, `published_at`
  - `capabilities`, `degraded_reasons`, `metrics`
- `knowledge_document_revisions`
  - 不可变的来源对象、内容校验和、解析元数据
- `knowledge_base_version_documents`
  - 版本与文档 revision 的 manifest

现有 chunks、实体、关系及实体引用增加 `version_id`，所有检索必须显式过滤版本。

逻辑知识库增加 `active_version_id`。删除或新增文档创建新 manifest，不物理破坏仍被会话引用的旧版本。旧版本通过保留策略和引用计数异步回收。

### 6.3 代码库版本

新增 `codebase_versions`：

- `id`, `codebase_id`, `parent_version_id`
- `state`, `build_id`
- `source_snapshot_key`, `source_revision`, `source_digest`
- `capabilities`, `degraded_reasons`, `metrics`
- `created_at`, `published_at`

文件、符号、边、检索 chunks 和 artifacts 增加 `version_id`。逻辑代码库增加 `active_version_id`。

每个可发布版本必须拥有不可变源码快照。Ask/Agent 的 `read_source` 从版本快照恢复或按需挂载，不依赖摄取沙箱长期存活。会话 Agent 的工作区副本与代码库发布版本分离；用户修改工作区后，回答需区分“发布版本证据”和“会话本地修改”。

### 6.4 构建记录

文档和代码可分别保留领域 Runner，但统一暴露构建记录：

| 字段 | 说明 |
|------|------|
| `build_id` | 构建 ID |
| `resource_kind/resource_id/version_id` | 构建目标 |
| `state` | `queued/running/succeeded/degraded/failed/cancelled` |
| `phase` | 当前阶段 |
| `progress` | 已完成/总量及百分比 |
| `error_code/error_detail` | 结构化错误 |
| `degraded_reasons` | 可重试的增强能力失败 |
| `started_at/heartbeat_at/finished_at` | 恢复与对账依据 |

同一逻辑资源默认只允许一个 active build。重复的 reindex/reanalyze 请求返回现有构建；若显式要求排队，则创建下一个 queued build，不能并发写同一版本。

## 7. 模式与能力治理

### 7.1 能力分类

每个工具在注册时必须声明：

```text
effect:
  read_only | workspace_write | external_write | interactive
idempotency:
  safe | idempotent_with_key | non_idempotent | unknown
approval:
  never | policy | always
concurrency_group:
  none | sandbox | browser | filesystem | integration:<name>
```

缺失声明时采用最保守策略：视为 `unknown + always approval`，且不得进入 Ask。

### 7.2 Ask 能力集

Ask 只允许：

- `message`
- `knowledge_search`、`knowledge_read_source`
- `code_search`、`code_read_source`
- 明确标记 `read_only` 且通过管理员允许列表的 MCP/A2A 集成

Ask 禁止：

- Shell、文件写入、浏览器执行
- Artifact 创建或修改
- 外部系统写操作
- Image generation 等产生持久化交付物的工具
- 能获得更宽权限的子 Agent

如果请求需要禁止能力，Agent 应解释原因并建议用户切换到 Agent 模式，不得静默绕过。

子 Agent 必须继承父会话 `CapabilityPolicy`，只能进一步收窄，不能扩权。Skill 的工具白名单同时受模式能力集约束。

### 7.3 Agent 能力集

Agent 可以装配所有经租户和用户授权的工具，但每次执行仍受：

- RBAC 与资源所有权
- 工具风险策略
- 会话级授权
- 调用级审批
- 沙箱与集成隔离
- 幂等及并发策略

文档知识库 Agent 使用已绑定知识版本作为依据，可在沙箱中生成报告、对比表和其他交付物；代码知识库 Agent 同理，并可操作会话工作区副本。

## 8. Agent 工具执行协议

### 8.1 预检与分批

模型返回多个 tool calls 后，执行器先对整个批次完成预检：

1. 解析并校验参数。
2. 校验 CapabilityPolicy、RBAC 和资源范围。
3. 计算审批需求、幂等策略和 concurrency group。
4. 将所有待审批调用持久化到 approval queue。
5. 若存在待审批调用，先发出一个结构化 `WaitEvent`，不执行任何有副作用调用。

审批完成后：

- 只读、互不冲突的调用可以并行。
- workspace write、browser、shell、external write 按 concurrency group 串行。
- 批准的是规范化后的工具名、参数摘要、目标和风险；参数发生实质变化必须重新审批。
- 拒绝、取消和超时均生成工具结果并回到 Agent，不能丢失已完成只读调用的结果。

### 8.2 审批队列

`pending_tool_call` 单值结构迁移为持久化 approval queue：

- 每个调用拥有稳定 `tool_call_id` 和状态。
- 支持一次批准整个批次或逐项批准。
- Resume 按 ID 消费审批，重复 Resume 幂等。
- 会话检查点保存队列、执行日志与已完成结果。
- 批次完成后才进入下一轮模型调用。

### 8.3 重试与幂等

- `read_only + safe`：允许对明确瞬态错误做有界重试。
- `idempotent_with_key`：必须把稳定 idempotency key 传到工具或集成后才可重试。
- `non_idempotent/unknown`：框架不自动重试；返回可恢复错误，由用户或 Agent 发起新的、可审计调用。
- 超时不等于未执行。对外部写操作超时后进入 `outcome_unknown`，先查询状态或请求用户确认。
- 工具执行日志记录 attempt、idempotency key、目标、开始/结束时间和结果摘要。

### 8.4 Flow 与任务终态

Flow 不再以“是否抛异常”隐式决定任务结果，必须返回 `RunOutcome`：

```text
status:
  succeeded | failed | cancelled | waiting
error:
  code, message, retryable, details
usage:
  token/tool/time counters
```

约束：

- `ErrorEvent` 必须对应 `failed` 或一个明确的可恢复步骤，不能随后无条件发出 completed。
- `waiting` 不是终态完成，Worker 保留可恢复上下文。
- Worker 仅依据 RunOutcome 写任务终态。
- SSE 事件序列必须通过状态机校验，终态之后拒绝写入其他终态。
- UI 以服务端持久化终态为准，不以局部事件猜测最终状态。

### 8.5 Worker claim 与恢复

- Redis dispatch 消息无论命中执行、发现已有 lease 或识别为重复，都必须走明确 ack/requeue 路径。
- `task_id + run_generation` 组成执行代次，旧代次不能覆盖新代次状态。
- Agent、KB build、Codebase build 都进入统一的 stuck-run 对账。
- 对账只能恢复未到终态且 heartbeat 过期的构建；发布事务本身必须幂等。

## 9. 文档知识库设计

### 9.1 Ask 与 Agent

- Ask：基于固定 KB version 做混合检索、原文读取和有引用回答。
- Agent：继承相同 KB version，可使用 Agent 工具生成交付物。
- KB-only Agent 不再被服务端强制改写成 Ask。
- 创建两种会话都必须校验资源所有权和版本是否已发布。

### 9.2 版本化摄取

一次新增、删除、替换或全量重建都生成候选版本：

1. 创建 document revision 和候选 manifest。
2. 仅解析发生变化的文档；未变化文档通过 manifest 复用旧 revision。
3. 在候选 version namespace 内构建 chunks 与关键词索引。
4. 尝试向量索引、GraphRAG。
5. 执行发布校验。
6. 在单事务中设置版本状态和 `knowledge_bases.active_version_id`。

任何步骤失败均不得清空或修改 active version 的索引。

### 9.3 文档状态

文档 revision 的状态拆分为：

- `uploaded`
- `parsing`
- `parsed`
- `indexing`
- `indexed`
- `failed`

`parsed` 不代表可检索。只有 version 发布后，manifest 中 `indexed` 的 revision 才对会话可用。成功重试必须清除旧 error/warning；失败信息归属于 build 和 revision，不能污染后续成功版本。

### 9.4 发布门槛与降级

必需：

- 至少一个文档 revision 解析成功。
- 成功文档已完成分块。
- 关键词索引完整且能通过抽样查询。
- manifest、chunks 和引用可在同一 version 下闭合。

可降级：

- 向量索引失败：设置 `vector_search=false`，保留关键词检索并自动重试增强构建。
- GraphRAG 失败：设置 `graph_search=false`，不影响核心检索。
- 部分文档失败：版本可发布，但需展示失败文档数及原因。

### 9.5 检索与引用

- 关键词与向量结果通过稳定融合策略合并；向量不可用时自动退化为关键词。
- GraphRAG 只引用当前 version 的实体与关系。
- 返回关系时必须同时返回可解析的两端实体，不得以内部 UUID 代替实体名。
- 每个引用包含 `version_id`、`document_revision_id`、页码或 chunk 范围。
- 原文预览支持游标分页，不以固定数量静默截断。

### 9.6 真实知识图谱

UI 图谱读取真实实体、关系和来源引用：

- 节点为实体，边为已持久化关系。
- 节点和边都可展开到来源文档位置。
- 查询结果中缺失端点时由服务端补全端点摘要。
- 未启用或构建失败时展示能力状态，不退化成“文档节点假图谱”。

实体唯一性由数据库约束保障，至少以 `(version_id, normalized_name, entity_type)` 唯一；写入使用原子 upsert，避免 select-then-insert 竞态。

### 9.7 成本控制

- GraphRAG 任务采用有界队列，限制单文档、单构建的 chunk 数、LLM 调用数和并发数。
- 超过预算时构建标记 `graph_partial`，记录未处理范围，可从检查点继续。
- 大文档解析、embedding 和保存均批处理，事件报告真实进度。

## 10. 代码知识库设计

### 10.1 Ask 与 Agent

- Ask：在固定 codebase version 上执行只读代码搜索、符号查询和源码读取。
- Agent：先把固定版本快照恢复到会话工作区，再允许修改、测试和生成交付物。
- 同一会话升级版本时，若工作区已有本地修改，必须先提示冲突风险；未经确认不覆盖本地文件。

### 10.2 版本化分析

1. 校验来源参数和所有权。
2. 在干净临时工作区物化 ZIP、FILES 或 Git 来源。
3. 生成不可变源码快照、来源 revision 和 digest。
4. 在候选 version 下收集文件、静态分析、构建关键词索引。
5. 尝试向量索引与证据图产物。
6. 校验候选版本。
7. 原子发布并更新 `codebases.active_version_id`。

重新分析请求必须幂等；同一 codebase 有运行中 build 时返回该 build，不允许并发清空或写入共享分析数据。

### 10.3 源码收集

- 每次物化使用全新目录，禁止复用残留工作区。
- 文件过滤在遍历阶段完成，忽略目录不能占用最大文件数配额。
- 采用批量读取或沙箱内分析，避免逐文件远程往返。
- 达到文件数、总字节数或单文件上限时，构建记录明确的截断状态和未覆盖范围。
- 没有可分析源码时构建失败，不能发布空 READY 版本。

### 10.4 混合代码检索

关键词检索为发布必需能力，至少覆盖：

- 文件路径和文件名
- 符号全名、短名和签名
- 源码文本
- 已验证的依赖边

向量检索可降级。正常模式下向量与关键词候选融合；向量失败或查询 embedding 失败时自动使用关键词结果，不返回伪装成“无相关代码”的空结果。

结果包含 `version_id`、文件 digest、路径、行范围、符号 ID 和匹配来源。

### 10.5 静态分析质量

- 符号 ID 至少包含语言、文件路径、限定名和位置，不能仅按名称去重。
- Python 调用边通过作用域、导入和限定名解析；无法唯一解析的边标为 `ambiguous`，不任取第一个同名符号。
- 非 Python 语言优先采用 AST/tree-sitter；暂不支持时，正则结果必须标注 parser 类型和低置信度，并保留真实代码范围。
- 只声明实际实现的边类型；未生成的 IMPORT/INHERIT 不应在能力声明中标为可用。

### 10.6 证据化产物

所有产物使用统一 `EvidenceRef`：

```text
version_id
file_path
start_line/end_line
symbol_id
analyzer
confidence
```

- 架构视图只能从真实模块依赖和入口证据聚合。
- 数据流只有在分析器能证明传递关系时生成。
- 调用链来自已解析调用边，不把函数列表顺序拼接成流程。
- 每条边可展开 EvidenceRef。
- 证据不足的视图不生成，并返回 `unsupported` 或 `insufficient_evidence`。

## 11. API 与事件契约

### 11.1 会话 API

创建会话支持：

```json
{
  "mode": "ask",
  "resources": [
    {
      "kind": "knowledge_base",
      "resource_id": "kb_123",
      "version_id": "kbv_456"
    }
  ]
}
```

`version_id` 可省略，服务端解析当前 active version 后在响应中返回实际绑定。旧字段兼容一个发布周期。

新增：

- `POST /sessions/{id}/resource-bindings/{kind}/upgrade`
- `GET /sessions/{id}/resource-bindings`
- `GET /knowledge-bases/{id}/versions`
- `GET /codebases/{id}/versions`
- 分领域 build 查询、重试和取消接口

升级接口必须显式给出目标 version，并返回受影响范围。历史消息不变。

### 11.2 构建事件

统一事件最小字段：

```json
{
  "event": "resource_build",
  "build_id": "build_123",
  "resource_kind": "codebase",
  "resource_id": "cb_123",
  "version_id": "cbv_456",
  "phase": "indexing",
  "state": "running",
  "progress": {"completed": 42, "total": 100},
  "degraded_reasons": []
}
```

事件写入持久化 event log，Redis 只负责实时分发。客户端使用 event cursor 重放，SSE 断线后不需要用猜测性轮询拼状态。

### 11.3 Run 事件

任务事件增加 `run_generation` 和显式 outcome。兼容期保留现有事件名称，但适配器必须保证：

- waiting 不映射成 completed；
- failed 后不再发送 completed；
- 重连可读取持久化终态；
- UI 忽略旧 generation 的迟到事件。

### 11.4 写接口语义

- 所有产生状态变化的接口使用 POST/PATCH/DELETE。
- 现有会改变快照或数据库的 GET 接口拆分为纯读取与显式准备动作。
- 旧写 GET 在兼容期返回弃用信息并内部委托新命令，之后移除。

## 12. UI 设计

### 12.1 统一资源入口

知识库和代码库详情页都提供：

- “开始 Ask”：严格只读、有引用的快速问答。
- “开始 Agent”：允许执行、写入和生成交付物。

入口展示将绑定的版本、核心检索能力和降级状态。无已发布版本时两个入口都不可用，并显示构建或修复动作。

### 12.2 会话版本提示

- 会话头部显示资源名称和版本。
- 有新版本时显示非阻塞提示，不自动切换。
- “升级上下文”展示版本差异摘要。
- 历史消息可查看当时使用的版本和引用。
- Codebase Agent 工作区有修改时，升级前弹出冲突说明。

### 12.3 构建状态

统一展示：

- 当前发布版本
- 正在构建的候选版本
- 阶段、进度和心跳
- 核心能力与增强能力
- 降级/失败原因
- 重试、取消和查看旧版本

摄取或重新分析进行中时，重复按钮变为“查看构建”，不能发起并发重建。

## 13. 安全设计

### 13.1 统一资源守卫

所有会话创建、资源变更、重建、下载、读取与升级路由调用同一 guard：

- 用户已认证
- 非 Auditor 才能执行写操作
- 资源属于当前 workspace/team
- 用户具备资源权限
- 请求版本属于该资源且已发布
- 模式与能力匹配

资源专用路由和通用会话路由不得各自复制一套规则。

### 13.2 源码与文件安全

- 读取路径先规范化，再验证位于版本挂载根目录内。
- ZIP 解压拒绝绝对路径、`..`、symlink escape 和超限压缩包。
- Git clone 使用参数数组或安全 SDK，不拼接 Shell 字符串。
- Git URL 只允许配置允许的协议、主机和端口；解析 DNS 后执行私网/环回/metadata 地址策略，重定向后再次校验。
- 上传文件与 FILES source 在服务层验证存在性、所有权和完整参数。

### 13.3 集成工具

- MCP/A2A 工具注册时必须声明 effect 和 idempotency。
- 管理员审核声明；未知工具不能用于 Ask。
- 外部写操作记录目标系统、资源、审批人、idempotency key 和结果。

## 14. 可观测性与审计

关键指标：

- Run 各终态数量、等待时长、恢复次数
- 审批队列长度、拒绝率、审批后失败率
- 工具执行 attempt 与疑似重复副作用
- 构建耗时、phase 停留时间、stuck build 数
- active version 切换成功/回滚次数
- 向量、图谱和产物的降级率
- 检索来源占比、空结果率、引用完整率
- 版本存储占用与 GC 数量

关键审计事件：

- 会话资源绑定与升级
- 构建创建、发布、失败、取消
- 能力拒绝
- 审批创建、批准、拒绝、过期
- 外部副作用执行及结果未知
- 资源下载和源码快照访问

日志中不得记录完整敏感文档、源码、密钥或工具参数；使用摘要与哈希。

## 15. 迁移与发布

### 15.1 数据迁移

1. 新增版本、构建和 binding 表以及 nullable `version_id`/`active_version_id`。
2. 将现有 KB/Codebase 当前数据登记为初始版本 `v1`。
3. 为现有会话回填 binding；无法证明历史精确版本时标记 `legacy_snapshot=true`。
4. 应用代码双读：优先版本字段，缺失时使用旧字段。
5. 新构建开始双写版本模型，旧客户端继续读取 active version 聚合。
6. 完成一致性校验后停止旧模型写入。
7. 经过至少一个兼容发布周期后再移除旧字段与路由。

迁移不得重新解释或删除用户当前数据。大表回填分批执行，索引并发创建，提供迁移前后计数与抽样校验。

### 15.2 分阶段交付

#### P0：正确性与安全

- CapabilityPolicy 与严格 Ask
- 工具预检、审批队列、重试策略
- RunOutcome 与终态状态机
- 版本表、会话绑定、原子发布
- KB READY 时序和 Codebase 并发重建
- 统一授权、路径与来源安全
- Agent/KB/Codebase stuck-run 对账

#### P1：检索与证据

- Codebase 关键词/向量混合检索
- KB 真实图谱和完整端点
- 证据化代码产物
- 持久化构建事件与引用版本展示

#### P2：性能与体验

- 批量代码分析与数据库写入
- GraphRAG 预算与断点续建
- 文档分页、版本差异和统一状态面板
- 旧版本 GC 和存储优化

每一阶段都必须可独立部署、迁移和回滚。P1/P2 不得延迟 P0 安全修复。

## 16. 测试策略

### 16.1 契约测试

- 每个工具注册都必须通过 effect/idempotency 声明测试。
- Ask 工具集合快照测试覆盖主 Agent、Skill、子 Agent、MCP 和 A2A。
- Flow event sequence 使用状态机性质测试，禁止双终态。
- 所有会话创建入口运行相同资源 guard 合约。

### 16.2 并发与故障注入

- 多 tool calls 中一个需审批时，验证其他副作用调用未执行。
- 多个待审批调用不会覆盖、重复消费或丢失。
- 非幂等调用超时后不会自动重试。
- 同一资源并发 reindex 只产生一个 active build。
- 在 parse、chunk、index、graph、artifact 和 publish 任一阶段注入失败，active version 始终可用且不变化。
- Worker lease 冲突、崩溃和 autoclaim 不会产生重复发布。

### 16.3 数据与检索

- KB 文档在 parsed 但未 indexed 时不可检索。
- 向量失败时 KB/Codebase 关键词检索仍返回结果并标记降级。
- 版本过滤不会混入其他版本数据。
- 删除文档或重建代码库后，旧会话仍能访问旧版本证据。
- 引用可解析到文档 revision 或源码快照的准确位置。
- 图谱和代码图每条边都具有证据；无证据输入不会生成图。

### 16.4 迁移与兼容

- 现有资源和会话成功回填。
- 新旧 API 在兼容期返回语义一致的 active version。
- SSE 旧客户端适配不产生 failed→completed。
- 回滚应用版本时旧字段仍可读取，active 数据不丢失。

## 17. 验收标准

P0 完成需同时满足：

1. 自动化测试证明 Ask 经主 Agent、子 Agent 和集成工具均不能产生写操作。
2. 审批前副作用调用数为零，非幂等工具框架重试数为零。
3. 所有 Flow/Worker 场景只产生一个终态。
4. 任意构建阶段失败后，上一 active version 的检索结果和源码读取保持不变。
5. 会话响应可查询到固定的 resource version 和证据。
6. KB 与 Codebase 在向量不可用时都能通过关键词检索返回结果并展示降级。
7. 并发 reindex/reanalyze 不会产生交叉写入或重复发布。
8. Auditor 和越权用户无法通过任何替代路由执行资源变更。
9. 源码读取无法越出快照根目录，恶意 ZIP/Git 来源被拒绝。
10. Agent、KB、Codebase 卡住任务都能被对账并收敛到可解释状态。

P1 完成需同时满足：

- 知识图谱只显示真实实体/关系并可回溯来源。
- 代码架构、调用和数据流产物的每条事实都有 EvidenceRef。
- Codebase 混合检索在 embedding 故障测试中保持可用。
- SSE 重连可从持久化 cursor 恢复构建与终态。

## 18. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 版本化导致存储增长 | 引用计数、保留策略、按 digest 去重和异步 GC |
| 迁移期间双模型产生差异 | 双读校验、指标比对、按资源灰度 |
| 工具治理降低 Agent 吞吐 | 只读并行，副作用按 concurrency group 最小化串行范围 |
| 第三方工具声明不可信 | 默认保守、管理员审核、运行时审计和租户 allowlist |
| 静态分析无法证明复杂动态关系 | 展示置信度与分析器，证据不足不生成 |
| 旧会话无法恢复精确历史版本 | 标记 legacy snapshot，避免声称完全可复现 |
| 原子发布事务过大 | 大数据提前写入 version namespace，发布事务只切换小型指针和状态 |

## 19. 后续实施文档

本 Spec 审计通过后，实施计划应拆为可独立验证的任务组：

1. 共享治理：CapabilityPolicy、ToolExecutionPolicy、RunOutcome、Worker 恢复。
2. 版本基础设施：binding、build、迁移与事件。
3. 文档知识库：版本化摄取、混合检索、真实图谱。
4. 代码知识库：不可变快照、混合检索、证据化分析。
5. UI 与兼容：双入口、版本升级、状态面板、旧接口适配。

每个任务以失败测试开始，包含迁移、实现、验证、文档和回滚检查。
