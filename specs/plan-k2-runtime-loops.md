# Plan K2 · 运行时循环改造

遵守总体 spec 决策 D4/D5/D6/D7。范围：`api/app/application/execution/`（workers/admission/run_control）、`api/app/infrastructure/execution/`（inbox/outbox/timer/activity/decision source）、`api/app/execution_kernel_main.py`、`api/app/composition/tasks.py`、`kernel.py`。依赖 K1 完成。

## K2-1 决策就绪模型（P0-1 根治，D4）

- `execution_run_projection` 表加列：`status`、`wait_reason`、`active_activity_count`、`decision_due_at`（nullable timestamptz）。formal projector 在 evolve 落投影时同步维护（K4 的投影改造先行接线此四列——本项与 K4-1 是同一批投影列改动，实施时一次做完）。
- `postgres_run_decision_source.py:load_ready`：WHERE 改为
  `terminal=false AND 未隔离 AND (status='queued' OR (status='waiting' AND wait_reason='retry' AND decision_due_at<=now()) OR (status='running' AND active_activity_count=0) OR decision_due_at<=now())`；ORDER BY 改 `decision_due_at NULLS FIRST, updated_at` 并保留 limit。
- 投影器置脏：活动结算、审批 settle/expire、timer 到期、RunRetried 等事件将 `decision_due_at` 置 now；idle 判定后由 decision_worker 清空（提交 lifecycle_command=None 的 Run 置 `decision_due_at=NULL`，需 decision source 回写接口）。
- 场景测试：100 个 WAITING(approval) Run + 1 个新 QUEUED Run，单轮 `load_ready` 必含新 Run（Postgres 集成测试）。

## K2-2 活动面韧性（P1-1，D5）

- `execution_kernel_main.py:157-170`：`_run_activity_plane` 套用与 `_run_lane` 等价的 per-lane try/except + 失败计数 + 退避。
- `activity_worker.py:148`：`gather(..., return_exceptions=True)`，逐项归类——基础设施异常（SQLAlchemyError/OSError/超时）对该 claim 走 `defer(retry_after=退避)`，未知异常记 metrics + defer，不再上抛杀 lane；`_execute_claim_traced` 的捕获集合补 `SQLAlchemyError`。
- 毒丸防护：activity 行加 `attempts` 计数（`postgres_activity_store.py`），超上限（默认 5）落 `dead_lettered` 状态 + 计数指标，决策侧收到 `ACTIVITY_DEAD_LETTERED` 失败结果（可重试标记 false）。

## K2-3 优雅关停排水（P1-2，D5）

- `composition/tasks.py:177-193`：`supervisor.stop()` 改三段式——置 stop 事件（workers 检查后停止新 claim）→ `await asyncio.wait(tasks, timeout=shutdown_timeout_seconds)` 等在途 handler 自然完成 → 超时才 `cancel()`。CRITICAL 语义保留（运行期 lane 死亡仍退进程），仅关停路径改变。
- `activity_worker` 增加 `stop_claiming()`（或检查共享 stop 事件）：run_once 在 stop 后跳过 claim、只收尾在途任务。
- `shutdown_timeout_seconds` 默认从 30s 提到 90s（覆盖典型模型调用），配置项已存在只改默认。
- 场景测试：在途 sleep 型活动 + SIGTERM，断言活动完成、Run 不进 FAILED。

## K2-4 Run 级重试退避（P1-3，D6）

- `run.py`（K1 后续小改，归本 plan）：`RunAttemptFailed` 的 decide 分支改为调度确定性 timer（uuid5，复用审批过期机制）投递 `RetryRun`，延迟 = `min(base * 2^retry_generation + jitter, cap)`（base 5s，cap 5min，进 operations policy 可配）；`decisions/base.py:71-73` 删除"WAITING+retry 立即 RetryRun"分支（由 timer 驱动，配合 K2-1 的 `decision_due_at`）。

## K2-5 队列 GC 与死信（P1-4，D7）

- `postgres_inbox.py`：claim 增加 attempts 上限（默认 10），超限转 `dead_lettered` 终态（不再被 claim）；新增 `purge_completed(before, batch)`。
- `postgres_outbox.py` / `postgres_timer_store.py` / `postgres_activity_store.py`：同样新增 purge 接口（delivered/已触发/终态行）。
- `job_scheduler.py` leader tick 新增 `execution_queue_retention` 步骤：inbox/outbox 保留 7 天、timer/activity 终态 30 天（config 新增 `execution_inbox_retention_days` 等四项），分批删除，节奏对齐现有 recycle_bin 步骤;dead_letter 行数进指标。

## K2-6 空转与惊群治理（P2-5）

- Redis wakeup：`redis_capabilities.py` 的 xread 改 consumer group（组名=lane，消费者=副本 id），事件只唤醒一个副本；组不存在时自动创建。
- `execution_kernel_main.py:205` 硬编码 1000ms block 接到 `execution_idle_poll_seconds`（配置语义恢复名副其实）；决策候选解码量随 K2-1 的 SQL 过滤自然大幅下降，不另做缓存。

## K2-7 进度与 TTL 修缮（P2-6/P2-7）

- `activity_worker.py:375-418`：progress dedupe_suffix 掺入 `claim.claim_generation`；同时按 K1-3 改写为直写 `execution_activity_progress` 表（不再走 inbox 命令路径，去重键即表唯一键，P2-6 随之消解）。
- 审批 TTL（P2-7）：`decision_worker.py:98-107` 的 RequestApproval payload 注入特判删除；`ttl_minutes` 改在聚合 `decide` 时从 `policy_snapshot` 读取（K1 已信封化），command payload 恒定，同 command_id 不再出现不同 envelope。

## K2-8 准入闸门与杂项（建议级）

- `admission.py`：`admit` 增加 per-scope 活跃（非终态）Run 上限检查（config `execution_max_active_runs_per_scope`，默认 200，0=不限），超限拒绝 `ADMISSION_LIMIT_EXCEEDED`——形成显式背压边界。
- `sqlalchemy_orchestrator.py:163-237`：乐观冲突 3 次重试加 10-50ms 抖动退避，锁内重读头版本。
- `activity_worker.py:126-151`：outcome/defer 路径改用 `datetime.now(UTC)`，不复用批次入口的陈旧 `now`。
- 幂等复核：`activities/remediation.py:31` 改 `idempotent = False`（修复动作有外部副作用，崩溃恢复不得盲目重放）；patrol 保持 True 并加注释说明"巡检检查为读主导，重放可接受"。

## 测试与验收

- 新增/改写：就绪过滤集成测试（K2-1 场景）、排水场景测试、退避 timer 单测、dead_letter 路径单测、GC 任务单测、consumer group 冒烟。
- 全量单测 + 集成测试 + `make lint`;本地栈实测 spec §4.3 的前两个场景。
