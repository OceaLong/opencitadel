# 前端 UI 架构

[English](frontend-ui.md)

Next.js 应用是执行内核的强类型命令与读模型客户端。Session、Run 工作台、分析工作区与
评估管理页面共享认证 API 契约；浏览器不承载执行状态机。

## 数据流

![前端数据流](../assets/diagrams/frontend-data-flow.png)

页面与组件收集用户意图，领域 Hook 管理请求生命周期，`ui/src/lib/api` 传输命令与
强类型查询。类型直接引用 `ui/src/lib/api/generated/schema.d.ts` 中生成的 OpenAPI Schema。

Session Timeline 使用脱敏公共事件的纯展示 Reducer。Run 工作台还读取服务端选择的投影
边界，事件订阅提示可能存在更新视图。SSE Feed Cursor 仅恢复订阅，不能作为回放的 `at`
Cursor。断线、重试与视图过期提示只影响展示，不改变正式执行状态。

## 执行工作台

Session 通过绑定来源的 Run 集合选择执行；`/runs/[id]` 也可以直接打开同一工作台。
任务视图与调试视图共享所选 Run、精确边界、Step、审批、产物及来源引用。Trace 分页固定
`at` 与投影 Revision，拒绝 Cursor 循环，并区分尚未加载的父节点、缺失历史与不完整历史。
迭代式布局与虚拟化渲染限制深层和大型 Trace 在可见区域的开销。

URL 保存 Run、视图、回放边界、Step、Panel、产物版本与引用选择。Local Storage 仅保存
按 Scope 隔离的面板尺寸、快捷键等布局偏好，不保存执行正文或回放授权。返回实时是一项
显式选择。新一轮对话与审批命令要求当前来源及 Run 授权，历史视图不能启用命令。审批写入
结果不确定时重新读取持久 Inbox；UI 不乐观宣告成功，也不自动重试命令。

Timeline 展示用户与助手消息、Activity 进度、审批等待、工具结果、正式错误、资源引用
及终态。Delta 仅合并至匹配的公共事件身份；未知类型保守展示且不能触发操作。VNC 提供
隔离沙箱的交互入口，不自行把 Activity 标记成功。正式 Run 活跃时拒绝删除 Session。

## 分析与评估页面

`/analysis` 获取固定 Summary Watermark，再使用同一 Capture 请求 Run 分页。图表使用
服务端的指标版本、单位、样本覆盖与保留的评估点，不另算评分或计费模型。比较工作区绑定
固定 Comparison Revision；Refresh 创建新 Revision。保留正文与异步产物 Diff Job 绑定该
比较。导出是调用者私有 Job，从固定筛选、比较或 Batch 选择创建，经认证 HTTP 查询与下载。
详见[执行读模型与分析](execution-analysis.zh-CN.md)。

`/evaluations` 管理 Dataset、Suite、Configuration、Rubric、Batch、评分、人工复核、
Recording 与 Environment。`EvaluationBoundary` 随认证 Scope Revision 重新挂载；
`useEvaluationTask` 串行执行表单操作并隔离过期回调。Batch Feed 刷新持久投影，不根据连接
状态推断执行或评分完成。

## 授权与内容边界

Workspace 通过 `X-Workspace-Id` 发送，服务端始终是权威。Auditor 页面只读，管理入口按
权限隐藏，但可见性不是授权。跨 Scope Not Found 与资源不存在在 UI 中不区分。

认证资源缓存由 `ClientDataProvider` 持有，键严格为 `userId + workspaceId`。
Logout 与 Workspace 切换在暴露新 Scope 前使旧 Generation 失效。工作台与正文 Hook 还
绑定 Run、边界、Scope Revision 与请求 Generation。授权丢失会清空保留正文及待完成下载，
晚到响应不能恢复旧数据。分析 Capture 在 Focus、Pageshow 与挂载期间每 30 秒重新验证；
来源身份变化或验证失败会使当前视图失效。

详情正文按有界 UTF-8 页读取。产物与来源保留原始不可变版本及 Provenance，不回退到当前
知识库文档。共享 `SafeArtifactPreview` 在惰性 Template 中按静态 HTML 白名单重建内容，
移除属性、可执行元素和资源加载内容，再由空 Sandbox 与严格 CSP 的 iframe 展示。完整下载
通过强类型分页路径完成，要求当前授权，并及时撤销浏览器 Object URL。

## 资源构建

知识库页面创建 Candidate Build、观察正式进度、按投影允许重试或取消，并原子 Publish。
Candidate 失败或取消时已发布版本仍可见。文档读取明确指定版本与文档 Revision；Session
上下文展示其精确已发布绑定。

## 国际化与质量

`ui/messages/en.json` 与 `ui/messages/zh.json` 是权威 Catalog。AST 检查器拒绝 Locale
错配、缺失或未使用键、未知动态调用、孤儿动态展开以及面向用户的硬编码文本。运行时 API
错误与通知键通过 `contracts/i18n-runtime-keys.json` 共享，并与 Python Emitter 验证。
CI 还运行 Prettier、TypeScript、ESLint、Vitest 与生产 Next.js Build。有界视图的实现不能
代替参考环境容量验收；AC21 尚未通过。

## 关键位置

- `ui/src/app/runs/[id]/run-page-client.tsx`：直接 Run 工作台入口
- `ui/src/hooks/use-session-runs.ts`：Session/来源 Run 选择
- `ui/src/hooks/use-execution-workbench.ts`：共享边界、生命周期与授权
- `ui/src/hooks/use-execution-detail-body.ts`：正文分页与失效
- `ui/src/lib/execution-view/`：URL 状态、Trace 加载/布局、事件订阅
- `ui/src/components/execution/`：任务/调试视图、详情、产物、来源与回放
- `ui/src/components/analysis/`：Capture、比较、图表与导出
- `ui/src/hooks/use-analysis-source.ts`：固定来源的周期性重新验证
- `ui/src/components/evaluation/evaluation-boundary.tsx`：Scope 与任务隔离
- `ui/src/components/session/safe-artifact-preview.tsx`：静态产物隔离
- `ui/src/lib/api/`：生成契约的 HTTP/SSE Adapter
- `ui/src/lib/data/scoped-resource-cache.ts`：Scope/Generation 缓存原语
- `ui/src/providers/client-data-provider.tsx`：认证缓存所有权
