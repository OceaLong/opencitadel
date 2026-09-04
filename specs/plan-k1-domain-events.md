# Plan K1 · 领域模型与事件流改造

遵守总体 spec 决策 D1/D2/D3。范围：`api/app/domain/execution/`、`api/app/infrastructure/execution/postgres_event_store.py`、`postgres_snapshot_store.py`、`models.py`、相关迁移与测试。**存量数据不迁移，本地库销毁重建。**

## K1-1 事件基线重置（D1）

- `run.py`：删除 `2 if name == "CreateRun" else 1`（:264,271）、`2 if event_type == "RunCreated" else 1`（:536、:1015）等全部版本 ternary；所有事件/命令注册为 `schema_version=1` baseline；各 `_decide_*` 发射点的 `event_schema_version` 改为引用 registry 常量而非字面量。
- `registry.py`：新增 `latest(name) -> int`、`assert_registered(name, version)` 查询接口；未知版本从抛 KeyError 改为抛领域错误 `UnregisteredEventError`（可被 orchestrator 捕获归类，消除事件侧崩溃/命令侧拒绝的不对称）。
- 新增 `api/app/domain/execution/EVOLUTION.md`：演进守则三条（禁止重定基线 / 同版本禁改 shape / 加字段必升版 + upcaster），并注明 `extra="forbid"` fail-closed 策略是有意为之、其代价（滚动发布需先发 upcaster 再发写入方）。

## K1-2 统一 upcast 管道（D2）

- `postgres_event_store.py`：`replay()` 与流读取在 `_to_stored` 之后统一调用 `event_registry.upcast(...)`——orchestrator 的 `_upcast_events`（sqlalchemy_orchestrator.py:309-329）删除，改为消费已升级事件；formal projector（postgres_formal_projector.py:373 的裸 `evolve`）自动获得同一管道。
- `internal_payload` 信封化：结构改为 `{"schema_version": int, "data": {...}}`；`run.py` 中读 policy_snapshot/semantic_payload/decision_data 的位置（:284/:302/:473）改为经 `internal_registry.upcast` 后再 `model_validate`；为 `RunPolicySnapshot` 等内部模型建立独立版本注册表（与事件 registry 同一套 Upcaster 抽象复用）。
- `_validate_event`（run.py:1010-1023）：expected 版本查 `event_registry.latest()`，不再硬编码。
- 聚合自检（P2-1）：`RunAggregate.__init__` 断言 `_EVENT_PAYLOADS` 每个键在 registry 有 latest 且 evolve 分支齐全（用 `_EVENT_PAYLOADS.keys()` 驱动一次干跑校验）；`decide` 产出的 NewEvent 在 append 前经 `assert_registered` + payload model 校验，写侧遗漏在写入前爆而非重放时爆。

## K1-3 遥测出流与 RunState 瘦身（D3，P1-7）

- 新表 `execution_activity_progress`（`infrastructure/execution/models.py` + 初始迁移直接加列/表）：`(run_id, activity_id, generation, sequence)` 唯一键，payload JSONB，append-only，无 hash 链、写入不经 scope advisory 锁。
- `run.py`：删除 `ActivityProgressed` 聚合事件及其 evolve/decide 分支与序列连续性校验（:442-455）；`ReportActivityProgress` 命令改由 activity_worker 直接写新表（K2 承接接线）；投影/SSE 读进度改读新表（K4 承接）。
- `RunState` 瘦身：`activity_results` 元素改为 `{activity_id, generation, status, result_ref, digest}`；完整 decision_data 写入 `execution_activity_outcome` 表（或对象存储，实施时按现有 content-addressed 输入通道就近选型），聚合只持引用。八个只增元组中凡是纯观测用途的（非决策输入）一并移出 state。
- `postgres_event_store.py` 的 64KB 单事件上限保留；因 decision_data 出流，正常路径不再逼近上限。

## K1-4 快照与哈希守卫（P2-3/P2-4）

- `snapshot_serializer_version` 随 RunState 瘦身 bump；`postgres_snapshot_store.py:110-117` 的失效指标拆 `schema_drift` / `corruption` 两个 reason（serializer 不匹配→drift，hash 不符→corruption）。
- 新增 golden-file 测试 `api/tests/app/domain/execution/test_canonical_golden.py`：固定构造的 RunState/事件 → 固定 hash 字节；pydantic 升级导致 dump 行为漂移时此测试先红。
- 新增 CI 守卫测试：RunState 字段集（`model_fields.keys()` 快照）变更必须伴随 serializer_version bump（快照文件比对）。
- `serialization.py`：canonical 哈希输入由透传 `model_dump(mode="python")` 改为显式构造原始类型字典（datetime 统一 isoformat 微秒、enum 取 value），削弱对 pydantic 内部行为的依赖。

## K1-5 死代码与注释

- 删除 `api/app/kernel/` 整目录（仅 __pycache__ 残留）；修正 `run.py:44-46` 过时的 "kernel DecisionWorker" 注释。

## 测试与验收

- 改写受影响单测（事件版本、progressed、activity_results 形状相关断言）；补 upcast 管道测试：注册 v1→v2 upcaster 后，orchestrator 重放与 projector evolve 走同一升级结果。
- 跑 `api` 全量单测 + Postgres 集成测试（库销毁重建后）；`make lint`。
- grep 断言：`event_schema_version=1` 字面量只出现在 registry 常量处；`ActivityProgressed` 在聚合模块零残留。
