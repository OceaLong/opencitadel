# 构建并使用内部知识库

本教程将创建版本化知识库、跟踪构建、启动固定版本的 Ask/Agent 会话，并在没有检索中断的前提下安全更新内容。

## 1. 创建逻辑知识库

打开 **知识库**，创建名称稳定的资料库，例如“研发手册”，选择所属工作区，并按需配置分块、检索、OCR 和 GraphRAG。

此时知识库可能还没有 active 版本。文档数量或 `ready_doc_count` 本身不足以开启生产问答；必须存在已发布的 `ready` 或 `degraded` 版本。

## 2. 添加不可变来源

上传支持的文件，或添加获准的 Web、Confluence、飞书 URL。服务端下载源内容、记录摘要，并创建不可变文档修订。添加内容会创建候选版本和持久构建，不会修改当前 active 版本。

候选清单复用未变化修订，并保存精确的 `(document_id, document_revision_id)`。源内容变化时会创建新修订，而不是覆盖旧字节。

## 3. 跟踪构建

资料库视图会显示 active 版本和 active 候选，也可以查看：

```text
GET /knowledge-bases/{kb_id}/versions
GET /knowledge-bases/{kb_id}/versions/{version_id}
```

流水线会报告解析、分块、关键词索引、向量、图、验证和发布进度。状态含义如下：

| 状态 | 含义 |
| --- | --- |
| `building` | 候选尚不完整，Ask 和 Agent 都不能使用 |
| `ready` | 已发布，所有配置能力可用 |
| `degraded` | 已发布；强制关键词/来源读取可用，可选能力禁用情况明确 |
| `failed` | 候选未发布；上一个 active 版本持续可读 |

修订处于 `parsed` 仍不可检索。必须等待候选版本发布，不能用 `ready_doc_count` 绕过检查。

## 4. 处理降级构建

向量或 GraphRAG 故障可能产生如实的 `degraded` 发布。请查看版本/构建的 capabilities 和 `degraded_reasons`：

- `vector_search=false` 表示检索继续使用关键词。
- `graph_search=false` 表示图浏览不可用，且不会返回半成品图。
- 解析、分块、关键词、验证或发布等强制阶段失败时，候选会失败，active 版本不变。

图接口本身以 `capability=false` 和空节点、空边表达不可用；详细原因位于版本/构建状态面。

## 5. 启动固定版本的 Ask 或 Agent 会话

在知识库中选择 **Ask** 进行聚焦问答，或选择 **Agent** 执行使用工具的工作流。两种模式都会解析一个明确的已发布版本，并将绑定与会话原子保存。

若 UI 或 API 提供版本选择器，可以选择已发布历史版本；否则选择当前 active 已发布版本。会话上下文会展示所选版本。

后续发布新版本不会改变已有会话。缺失、跨资源、重复、building、failed 或未发布绑定都会被 runner 拒绝。

## 6. 核对引用并读取精确来源

回答中的引用精确标识被索引证据：

```text
(version_id, document_revision_id, doc_id, page_no, chunk_id)
```

没有页码元数据的来源允许 `page_no` 为空。打开引用时使用版本化来源接口：

```text
GET /knowledge-bases/{kb_id}/versions/{version_id}/documents/{doc_id}/content
```

可以使用页码过滤，也可以用响应中的 `next_cursor` 继续。响应还包含
`document_revision_id`、有序内容项、`total` 和 `truncated`。游标不能跨知识库、版本、文档、修订或页码过滤条件复用。

因此，即使新版本已发布，旧会话仍能打开当时引用的完全相同来源。

## 7. 浏览知识图

对 `graph_search=true` 的已发布版本，打开图面板，或调用：

```text
GET /knowledge-bases/{kb_id}/versions/{version_id}/graph?q=term&limit=50
```

返回 `cursor` 时可继续翻页。节点是真实抽取实体，边连接返回集合中的真实实体端点，边证据可回到精确来源分块。若
`capability=false`，应改用关键词/向量检索，不能把空图解释成“没有关系”。

图处理受分块数、LLM 调用数、Token、并发度和截止时间预算约束。超出预算会降级图能力，但不阻塞关键词发布。

## 8. 更新、重建或移除内容

所有变更都创建候选：

- **新增**：在 copy-on-write 清单中加入新修订。
- **重建**：从 active 清单重建，不清空 active 数据。
- **移除**：从下一个清单排除文档，不同步擦除历史证据。

旧 active 会一直可查询，直到候选验证通过并原子发布。如果候选失败，已有 Ask/Agent 会话和继续使用旧 active 的新会话都不受影响。

同一知识库只能有一个 active 候选；完全相同的重复命令具备幂等性。

## 9. 重试、取消与恢复

对于失败构建，选择 **重试** 或调用：

```text
POST /knowledge-bases/{kb_id}/builds/{build_id}/retry
```

重试会从失败候选的不可变清单创建新候选，失败版本仍保留在审计历史中。

对于 active queued/running 构建，选择 **取消** 或调用：

```text
POST /knowledge-bases/{kb_id}/builds/{build_id}/cancel
```

该请求在 Run 中记录取消，并在 Activity 边界停止。请继续观察构建状态直到进入终态。如果 Activity Claim 过期，执行内核会安全继续持久构建，或将其标记失败，且不会改变 Active 版本。

## 10. 显式升级会话

新版本发布后，已有会话仍保持原绑定。使用会话上下文中的升级操作创建新的 current 绑定；旧绑定以
`is_current=false` 作为历史保留，使过去事件和引用仍可复现。

只有在希望后续轮次使用新快照时才升级。对于调查或合规工作流，继续固定旧版本可能才是正确选择。

## 11. 保留策略

版本垃圾回收默认关闭。启用后按保留数量、最短年龄和批量大小运行。active 版本、非终态构建候选，以及被当前或**历史**会话绑定引用的所有版本都会受到保护。

启用 GC 前：

1. 明确审计保留策略。
2. 确认旧引用和来源翻页仍工作。
3. 观察构建恢复和 GC 指标。
4. 使用保守的数量和年龄配置启用有界回收。

因此，从当前版本移除文档不等于立即物理删除。只有满足保留与绑定安全规则后，未引用数据才会被回收。

## 运维检查清单

- 只从已发布的 `ready` 或 `degraded` 版本开始问答。
- 以 capabilities 与降级原因为准，不能只看知识库顶层状态。
- 使用版本化引用和来源接口。
- 重建和移除在发布前都必须保留 active 版本。
- 通过持久构建标识执行重试或取消。
- 显式升级会话。
- 未明确保留和审计要求前保持 GC 关闭。
