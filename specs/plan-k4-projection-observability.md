# Plan K4 · 投影韧性、观测闭环与边界修缮

遵守总体 spec 决策 D12/D13/D14。范围：`api/app/execution_kernel.py`、`execution_kernel_main.py`、`infrastructure/execution/postgres_formal_projector.py`、`postgres_run_projection.py`、`postgres_public_projection.py`、`run_control.py`、`infrastructure/adapters/execution_ports.py`、`composition/`、`observability/execution_metrics.py`、`pyproject.toml`（import-linter）、`infrastructure/external/sandbox/factory.py`。依赖 K1/K2。

## K4-1 投影韧性（P1-13，D12）

- `execution_kernel.py:116-128`：`run_pending_projectors_once` 改 per-scope try/except；scope 连续失败计数（内存 + `execution_poisoned_scopes` 表，字段 scope、first_error、failure_count、quarantined_at），超阈值（3 次）隔离——`postgres_owner_scope_source.py` 的待处理查询排除已隔离 scope，其余 scope 不再被饿死；隔离事件计 metrics + error 日志。
- 与 K2-1 同批：formal projector 维护 run 投影新四列（status/wait_reason/active_activity_count/decision_due_at）；进度读路径改读 `execution_activity_progress` 表（K1-3）。
- rebuild 可运维（P2-14）：新增 CLI `python -m app.rebuild_execution_projection --scope <id>`（复用 rotate_db_signing_secret 的入口模式）；rebuild 前对该 scope 打"重建中"标记（poisoned_scopes 表加 rebuild 状态），`postgres_run_context_source.py:31-32` 遇 record 缺失且 scope 在重建中 → 抛可重试错误（activity_worker 走 defer）而非 `RuntimePolicyIntegrityError` 永久失败；重建完成清标记 + 解除隔离。
- checkpoint 锁（P2-18）：`postgres_formal_projector.py:1020-1026` 改 `with_for_update(skip_locked=True)`，拿不到锁即让位。

## K4-2 审批通知持久化（P2-15）

- `postgres_formal_projector.py:229-256` 的 best-effort dispatch 删除；改为投影事务内写 outbox 行（destination=notification，dedupe_key=uuid5(approval_id, kind, 受众)），由 outbox dispatcher 投递到 notifier——崩溃后可重投，天然去重。approval_expired 同路径。

## K4-3 观测闭环（P1-14/15，D13）

- `execution_kernel_main.py`：heartbeat 协程周期调用 `runtime.refresh_metrics()`（间隔=心跳间隔，失败只告警不杀 lane）；新增行为级测试：驱动一轮心跳后断言各 Gauge 被 set。
- 四条 lane（inbox/outbox/timer/projector）补 OTEL span（`lane.run_once`，属性含批次大小/耗时）与 Histogram；`projection_hash_mismatch` 等已枚举 reason 补齐调用位点（投影 hash mismatch 处 raise 前先计数）。
- SSE 显式化：`run_control.py:69-96` idle 超时前发 `{"type":"stream_timeout"}` 事件再关闭；轮询改挂 `execution:wakeup` 提示（复用 chat 侧 shared.py:682-686 模式），无提示时退化 1s 轮询（替代 0.2s 常轮询）。
- status 端点：暴露 per-scope 投影 lag（scope head_position − checkpoint position）与隔离 scope 列表。
- 快照失效指标分标签已在 K1-4；本 plan 校验 dashboards/告警文档提及（specs 内备注即可，不动 helm）。

## K4-4 投影性能（P2-17）

- `postgres_formal_projector.py:358-463`：按 run_id 聚批——同批次同 Run 的事件序列先在内存 fold，末次统一 UPSERT（state JSON/hash 只算一次），DB 往返从 O(事件数×4) 降到 O(涉及 Run 数)。
- `postgres_run_projection.py:106-133`（approval_stats）与 `:232-260`（governance_daily）改 SQL 聚合（AVG/COUNT + date_trunc + GROUP BY），删除全表内存聚合；system 身份下补租户过滤参数。

## K4-5 分层边界修缮（P2-13，D14）

- `build_execution_kernel_runtime`（execution_ports.py:234-312）整体迁至 `app/composition/kernel_runtime.py`；`execution_ports.py:31` 对根模块 `app.execution_kernel` 的反向 import 随迁移消除，文件回归纯适配器；`infrastructure/external/session_list_notifier.py:13` 对 `app.composition.tasks` 的依赖改为构造注入。
- `pyproject.toml` import-linter 补契约：`infrastructure` 禁止 import `app.composition`、`app.execution_kernel`、`app.execution_kernel_main`、`app.observability`（violations 先清后加）；第 5 条契约去掉 `allow_indirect_imports = true`，清理暴露出的间接依赖。
- 双进程语义（P2-16）：kernel 组装（composition/kernel.py + shared.py:441-460）为 CommandIngress/RunProjection/PublicProjection 显式传入系统 authorization（新建 kernel 专用实例，禁 None→contextvar 回退进入 kernel 图；API 图保留每请求身份路径）；`sandbox/factory.py` 拆 `AttachOnlySandboxFactory`（API 进程装配，create() 抛 NotSupported）与 `PooledSandboxFactory`（kernel 进程），杜绝 API 侧静默冷启动。
- 游标密钥（建议级）：`shared.py:455` 的 `sha256(api_key_secret)` 派生改独立配置 `PUBLIC_CURSOR_SECRET`（空则回退派生值，双密钥解码兼容轮换——此处的"兼容"是运行期密钥轮换需要，非历史包袱）。
- 死代码：删除 `application/execution/formal_projector.py` 门面（零调用方）；`postgres_formal_projector.py:226` 等处捕获集合补 `SQLAlchemyError`，rollback 分支真实可达。

## 测试与验收

- 新增：坏 scope 隔离集成测试（写坏一行投影 → 其他 scope 照常推进 + 隔离表有记录）、rebuild CLI 冒烟（重建后 hash 一致、标记清除）、通知 outbox 化后崩溃重投测试、metrics 刷新行为测试、import-linter 全绿、SQL 聚合结果与旧实现等值的对拍单测。
- 全量回归：api 单测 + 集成 + ui + `make lint` + `make quality-check` + 验收 27/27（本 plan 是收尾位，承担 spec §4.3 全部五个场景实测）。
