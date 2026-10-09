# 执行读模型与分析

[English](execution-analysis.md)

执行可视化是执行内核的读侧。Run 工作台、分析汇总、比较与导出读取按 Scope 隔离、带版本
的事实；这些读模型不决定 Run、Invocation 或评估结果是否成功。

## 读模型流转

![执行读模型流转](../assets/diagrams/execution-read-model.png)

PostgreSQL 执行视图投影组合正式执行事实与脱敏公共进度观察。`PlaybackBoundary` 记录所选
Run、观察顺序、Formal/Progress Position、Projector Version 与 Projection Revision。
实时与历史视图据此重建同一组公共实体。Checkpoint 与 Shadow Generation 加速读取；缺失
区间与不完整字段仍由 `completeness` 明确表达。进度观察不替代正式终态或计费事实。

API 提供 Run List、组合 View、Step List/Detail、Timeline、Event 与有界正文读取。
契约要求共享切面时，这些资源使用同一所选边界。回放、Step 分页、正文分页和 SSE 恢复
Cursor 各有用途，不能互换。调用者原样保存 Cursor、边界与 Revision，不解析或自行构造。

## Scope 与当前授权

所有路由从服务端认证上下文获取 Principal 与 Workspace Scope。执行读取权限、Owner/Team
约束及数据库授权始终生效。带签名的强类型数据库操作提供分析与比较事实；Runtime 不拥有
私有 Capture 表的直接读取权限。

Capture 事务使用 `REPEATABLE READ` 收集一致事实集；当前授权检查使用新的
`READ COMMITTED` 事务。不可变 Capture 固定事实，不冻结权限；成员、覆盖或资源授权
变化后，调用者不能凭旧 Capture 继续读取。分析在 Capture 后与 Run 分页前后检查当前授权；
比较在组装保留详情及对齐后检查。正文与导出路径分别设置当前授权检查点。

授权或覆盖变化时拒绝继续读取。保留数据缺失明确返回不可用或要求刷新，不静默换成新的
实时选择。Retention Pin 在 Owner 有效时保留比较的原始资源，不额外授予访问权。

## 分析 Capture 与图表事实

`GET /execution-analysis/summary` 解析明确筛选、时间范围、小时/日粒度与时区。
已配置 Workspace 时区偏好时优先使用偏好，同时验证请求时区。返回不透明 Watermark、指标
版本、Coverage、采集时间与强类型指标。复用 Watermark 必须匹配查询与指标版本；缺少必要
图表事实的旧 Capture 要求刷新，不从当前数据补算历史。

调用者绑定的应用缓存最多保留 32 个 Capture、有效 30 秒；缓存命中仍重新检查授权。
数据库 Capture 声明 15 分钟 TTL、每调用者最多 20 个活跃 Capture、最多 100,000 个主要
成员及 1,000,000 个计费成员。这些是实施限制，不代表完整参考负载已通过验收。

`GET /execution-analysis/runs` 对固定 Capture 的 Run 成员分页，每次最多 200 条。
加密 Cursor 绑定 Scope、调用者、Watermark 与 Ordinal。图表事实和标量评估行由有界内部
数据库操作采集，随 Summary 的强类型 Charts 与 Evaluation Series 返回；没有独立的公开
分析图表点 API。评估 Batch Summary 另有 Cursor 分页契约。

指标保留单位、分子、分母、样本数、缺失数与排除数。成功、取消、未知结果、执行错误及业务
错误分开统计。计费明确选择 `run`、`selected_result` 或 `batch_total` 粒度，并区分
Subject 与 Judge 物理调用用量。评估 Series 复用 Evaluation Snapshot 推导，保留评估
Revision、Usage Watermark、评分来源、维度、Rubric 与成本口径。

可比较身份包括 Run Family、Dataset Version、执行模式、Environment Version、Rubric、
指标版本、评分来源、维度与适用维度集合。配置比较在同一可比较分层内使用配对 Case 均值。
固定种子的 Bootstrap 区间仅在配对样本足够时提供，解释为描述性统计。UI 保留缺失观察并
选择稀疏展示：少于 8 个有观察的趋势桶用离散点，少于 20 个延迟样本用精确值，少于 5 个
Case 用点而非箱线图，少于 12 个完整 Case 用表格而非散点图。

## 固定比较 Revision

`POST /execution-comparisons` 采集显式 Run 或全部匹配成员。Materialization 在同一事务
获取 Resource Pin、采集保留评估点并发布 Revision。读取要求精确 Revision，使用绑定
Owner 的 Cursor 分页成员；最多 5 个所选 Run 可附带保留详情。

Refresh 命令携带预期 Revision，创建新的不可变 Revision。手工 Step 对齐使用独立预期
Alignment Revision 与幂等请求身份，不改写底层执行切面。自动对齐以保留的 Step 身份生成
建议，确认前仍为建议。产物 Diff 是绑定精确保留版本的异步租约 Job，输出页使用独立 Cursor。

保留的输入、输出与产物正文按有界 UTF-8 页读取并脱敏；正文访问前后检查当前授权。
预览限制或二进制正文不可用需明确展示，不能回退到更新版本或另一个 Run。

## 导出生命周期

`POST /execution-analysis/exports` 接受调用者私有的筛选、比较或 Batch 选择并返回异步
Job。筛选导出固定选择，比较导出指明 Revision，Batch 导出携带评估切面。请求 Fingerprint
防止同一幂等键静默接受不同意图。Auditor 不能创建导出或修改比较。

Worker 获取持久租约、按有界页读取、在持久写意图下生成私有不可变 Chunk，并在发布 Manifest
前验证当前授权。Downloader 获取使用租约，把按序 Chunk 的尺寸与 SHA-256 摘要验证到匿名
本地 Spool；Provider 读取后重新获取授权证明，再释放字节。下载在开始流式输出已验证 Spool
前释放 Retention Use。Endpoint 返回认证的单个 CSV/JSON 响应，设置
`Cache-Control: no-store`，不提供公共对象存储 URL。CSV 中存在表格公式风险的文本会转义；
强类型数值列保持数值编码。

## 实现与验证边界

下列模块已实现读模型、分页、授权、保留、比较与导出行为。完整 AC21 容量验收仍需指定参考
环境及多轮原生证据，包括采集、清理与复用闭环；有界查询和单项压力测试不能代替该验收。

## 关键位置

- `api/app/application/services/execution_view_service.py`：公共 View 与 Cursor 契约
- `api/app/infrastructure/execution/postgres_execution_view.py`：边界重建与 Generation
- `api/app/application/services/execution_analysis_service.py`：调用者缓存与当前授权检查
- `api/app/infrastructure/repositories/db_execution_analysis_repository.py`：签名 Capture 与指标组装
- `api/app/infrastructure/repositories/db_analysis_native.py`：固定成员分页
- `api/app/infrastructure/repositories/db_analysis_points.py`：有界评估点采集与 Pin
- `api/app/domain/analysis/`：图表事实、可比较身份、指标与保留点适配
- `api/app/application/services/execution_comparison_service.py`：Revision、刷新、对齐与 Diff Job
- `api/app/infrastructure/repositories/db_execution_comparison_repository.py`：Materialization 与保留
- `api/app/application/services/comparison_body_service.py`：保留正文授权与脱敏
- `api/app/application/services/execution_export_worker.py`：租约生成与 Manifest 发布
- `api/app/application/services/execution_export_download.py`：已验证的私有下载
- `api/app/interfaces/endpoints/execution_{view,analysis,comparison,export}_routes.py`：HTTP/SSE 边界
- `ui/src/components/analysis/` 与 `ui/src/lib/analysis-view/`：固定 Capture 展示与导航
