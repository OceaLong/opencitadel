# 知识库版本化摄取

OpenCitadel 将知识库视为一个稳定的逻辑资源，其可读内容则来自不可变的已发布版本。重建不会原地修改当前索引，而是先创建候选闭包、完成验证，最后仅在闭包可安全读取时原子切换
`knowledge_bases.active_version_id`。

所有检索、引用、来源展开、GraphRAG、Ask Run 与 Agent Run 都必须携带明确的已发布 `version_id`。生产环境不存在无版本读写路径。

## 标识与存储模型

| 概念 | 标识与职责 |
| --- | --- |
| 知识库 | 稳定、受所有权范围保护的资源，并指向当前已发布版本 |
| 知识库版本 | 不可变候选或已发布快照；记录父版本、构建、能力、降级原因、指标和发布时间 |
| 逻辑文档 | 知识库内稳定的文档元数据 |
| 文档修订 | 对应一次确定源内容摘要及处理状态的不可变对象 |
| 版本清单 | 从版本到精确 `(document_id, document_revision_id)` 的有序映射 |
| 分块和图数据 | 同时携带 `kb_id` 与 `version_id` 的派生数据，不能跨版本推断复用 |
| 源 Run | 候选版本生命周期和进度的唯一权威，通过正式资源构建投影查询 |
| 会话绑定 | 将 Ask 或 Agent 会话固定到某个明确已发布版本的不可变记录 |

只有当清单中的每一项都能解析到精确修订，且强制派生数据完整时，版本才构成可读闭包。父子分块、关键词、向量、实体、关系和证据引用都必须按绑定版本过滤。

## 状态机

文档修订状态：

```text
uploaded -> parsing -> parsed -> indexing -> indexed
                    \              \-> failed
                     \-> failed
```

`parsed` 仅表示源内容提取成功，不表示可被检索，也不允许据此创建会话。生产问答只能读取已发布闭包中的 `indexed` 修订。

知识库版本状态：

```text
building -> ready
         -> degraded
         -> failed
```

`ready` 和 `degraded` 是可发布终态。`degraded` 必须如实表达：强制的关键词检索与来源阅读仍可用，但一个或多个可选能力被禁用，并由版本与正式 Run 状态面展示具体原因。

源 Run 使用 `new`、`queued`、`running`、`waiting`、`completed`、`failed`、`cancelled`。候选版本的 `ready`/`degraded` 是产品能力状态，不是第二套执行生命周期。取消命令由内核持久化，Activity 在 fencing 边界停止，未发布候选标记为 `failed`，Run 标记为 `cancelled`。

## 候选构建流水线

新增、移除、重建和重试都只操作候选版本：

1. 锁定知识库变更边界，创建一个 `building` 版本（父版本为当前 active），并原子接入其 `kb_ingest` Run。
2. 复制父版本清单，再应用新增或移除。未变更修订按标识复用；源字节变化时创建新的不可变修订。
3. 解析变更修订并持久化提取状态。
4. 构建父子层级分块。
5. 构建强制关键词索引。
6. 在显式预算内按需构建向量与知识图。
7. 验证完整候选闭包及其标识约束。
8. 在同一事务中比较预期父版本与当前 active，收敛候选状态，并通过切换 `active_version_id` 发布。

发布时的 compare-and-swap 防止过期并发候选覆盖新版本。命令收件箱和 `request_key` 保证接入幂等，数据库强制每个资源最多一个 building 候选。

## 失败语义

| 失败位置 | 候选 / Run 结果 | 当前版本 |
| --- | --- | --- |
| 解析 | failed | 不变且持续可读 |
| 分块 | failed | 不变且持续可读 |
| 关键词索引 | failed | 不变且持续可读 |
| 闭包验证 | failed | 不变且持续可读 |
| 发布 CAS 或事务提交 | failed | 不变且持续可读 |
| 向量索引 | 以 `degraded` 发布，`vector_search=false` | 原子前移 |
| 图抽取、预算或截止时间 | 以 `degraded` 发布，`graph_search=false` | 原子前移 |

强制阶段失败绝不清空 active 分块，也不会造成检索黑屏。可选阶段失败不能伪装能力可用；未完成图数据不会作为“半张图”暴露。

当前降级原因包括 `DOCUMENT_PARTIAL`、`EMBEDDING_UNAVAILABLE` 以及 GraphRAG
失败/预算原因。应从版本或正式 Run 状态读取。图接口在图能力不可用时只返回
`capability=false` 和空节点、空边。

## 检索与会话一致性

Ask 和 Agent 创建会话时都先解析已发布版本，在最终事务边界再次检查，并与会话一起持久化唯一的知识库绑定。派生文档计数只是展示值，不是授权条件。

runner 会再次校验持久绑定，并将同一个 `version_id` 传给 retriever 与知识库工具。缺失、重复、资源不匹配、`building`、`failed` 或未发布绑定都会 fail closed。发布新版本不会静默改变已有会话。

显式升级会话时，系统创建新的 current 绑定，并将被替代绑定保留为历史，从而保证审计和旧事件日志可复现。

## 引用与来源展开

每个检索结果和图证据都携带完整标识：

```text
(version_id, document_revision_id, doc_id, page_no, chunk_id)
```

对没有页码元数据的源格式，`page_no` 可以为空；其余标识仍必须解析到精确版本闭包。来源展开使用：

```text
GET /knowledge-bases/{kb_id}/versions/{version_id}/documents/{doc_id}/content
```

响应包含解析后的 `document_revision_id`、有序内容项、`next_cursor`、`total` 和
`truncated`。游标绑定知识库、版本、文档、修订和页码过滤条件；改变其中任意字段后都不能复用旧游标。版本化接口是引用来源查看器的权威入口。

## GraphRAG

图抽取生成真实实体节点、关系边以及指向真实分块的证据。接口为：

```text
GET /knowledge-bases/{kb_id}/versions/{version_id}/graph
```

接口接受 `q`、`cursor`、`limit`，返回真实实体端点，不合成文档占位节点。每条边的端点都必须出现在返回节点集合中，证据使用同一五元引用标识。

图构建受 `max_parent_chunks_per_doc`、`max_chunks`、`max_llm_calls`、
`max_tokens`、并发度和持久截止时间约束。持久进度标记保存候选版本、游标、累计调用/Token
以及截止时间，重试不能重置预算。触达上限、超时或抽取失败会降低图能力，但不阻塞强制关键词索引发布。

## 命令与运行恢复

受所有权范围保护的 API 包括：

```text
GET  /knowledge-bases/{kb_id}/versions
GET  /knowledge-bases/{kb_id}/versions/{version_id}
POST /knowledge-bases/{kb_id}/builds
POST /knowledge-bases/{kb_id}/builds/{build_id}/retry
POST /knowledge-bases/{kb_id}/builds/{build_id}/cancel
POST /knowledge-bases/{kb_id}/reindex
```

同一知识库同时只能有一个 active 候选。完全相同的命令具备幂等性。重试会基于失败候选的不可变清单创建新候选，而不是复活或覆盖旧版本。重建从 active 清单创建候选，绝不调用原地
`clear_index_data`。

移除文档只修改下一个候选清单，不会同步物理删除逻辑文档、修订、分块、图证据或旧版本。只有移除候选发布后 active 才会切换。

执行内核从 PostgreSQL 回收过期 Activity claim 和待处理命令。知识图预算/cursor 指标属于候选进度标记，Run 进度只在正式投影中。恢复会安全继续同一 invocation，或在不改变 active 版本的前提下收敛候选。

## 保留与垃圾回收

版本 GC 默认关闭：

```yaml
knowledge_base:
  version_gc_enabled: false
  version_retention_count: 10
  version_retention_min_days: 30
  version_gc_batch_size: 50
```

scheduler 在 leader lease 下执行有界 GC。当前 active、被非终态 Run 引用的候选，以及被任意会话绑定引用的版本（**包括 `is_current=false` 的历史绑定**）都是本次回收的永久根。父版本指针对 GC 安全，删除顺序维护图数据、分块、清单、修订和逻辑文档的外键。只有没有任何保留版本引用时，共享修订/文档才可回收。GC 会报告保护数量以及回收行数/字节。

启用 GC 前，应先观察一个无回收的运行窗口，并根据审计要求设定保留数量与最短天数。

## 全新 schema 契约

初始 schema 直接把版本 identity、manifest 闭包、`build_id` 和 `request_key` 建成必填数据，不创建无版本内容权威，也不创建独立资源构建生命周期表。首次生产发布前允许破坏式 schema 演进；后续迁移必须维持候选版本与 Run 的职责分离，不得引入过渡性双写。
