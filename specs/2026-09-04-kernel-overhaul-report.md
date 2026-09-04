# 执行内核彻底改造 · 执行报告（2026-09-04）

依据 `2026-09-03-kernel-overhaul-spec.md`（14 条架构决策 D1–D14）与 plan-k1..k4。
四个 plan 全部落地，覆盖架构审计的全部 34 项发现（1 P0 / 15 P1 / 18 P2 + 建议项）。

## 最终验证（全绿）

| 验证 | 结果 | 基线对比 |
|---|---|---|
| api 单测 | **1842 passed / 0 failed** | 改造前 1747 |
| Postgres 集成（一次性 pgvector 库，重建 schema） | **466 passed** | 改造前 116 |
| make lint / make quality-check（含 import-linter、i18n gate、ui） | 通过 | — |
| **验收套件（--disposable 全新卷）** | **27/27，0 skip 0 fail，36 项覆盖需求全 passed** | — |
| 真实栈内核链路实测（无头探针） | CreateRun→StartRun→活动执行→重试 timer 两轮退避→终态，全链路验证 | — |

改动 159 个文件全部 `git add` 暂存、未 commit；patch 备份于仓库外
`/Users/longhaiyang/code/agent/kernel-overhaul-final.patch`（15776 行）。

## 各 plan 落地摘要

- **K1 领域模型与事件流**：事件/命令基线全部重定 v1；upcast 下沉 EventStore 读边界
  （public+internal 双 payload 单管道）；ActivityProgressed 彻底出流（公共事件表自有
  seq 主键 + 进度 sink）；decision_data 出流至 activity task 行（事件只留 sha256 摘要，
  决策源水合 + 摘要校验）；快照失效指标分 drift/corruption；EVOLUTION.md 守则 +
  schema 守卫测试（golden hash / 字段集↔serializer 配对 / 注册表自检）；app/kernel 死包删除。
- **K2 运行时循环**：P0 根治（投影三列 + decision_due_at 就绪过滤 + disarm 守卫，
  实测 100 挂起审批不饿死新 Run）；活动面 gather 隔离 + 死信上限；关停三段式排水
  （发版不再杀在途调用）；Run 重试确定性 timer 退避（5s·2^gen，cap 300s）；四队列
  GC + 保留期（活动行仅清已终态 Run）；wakeup consumer group 去惊群；admission
  per-scope 上限；乐观冲突退避。
- **K3 扩展面**：工具契约 v2（ToolInvocationError 四类归一化为 tool result 喂回模型，
  参数幻觉不再杀 Run）；CatalogSnapshot 指纹一致性（目录漂移→not_found tool error）；
  activity_types 常量单源 + DECISION_PLANNERS 声明式注册 + 启动交叉断言；ToolSpec
  装配表（双清单/PatrolTool/register_schema/for_child 删除，**Vision 工具上线生产**）；
  MCP UNTRUSTED 包裹 + inputSchema 限制 + 连接池 per-fingerprint 锁与失效重建；
  skill allowed_tools None/[] 语义 + 真 YAML frontmatter + a2a 组单源；search 能力单源；
  on_cancel 取消传播（shell kill / browser cleanup）。
- **K4 投影观测边界**：execution_poisoned_scopes 表 + per-scope 隔离（连败≥3 隔离，
  其余 scope 照常推进）+ rebuild CLI + 重建窗口 defer；审批通知 outbox 化（崩溃可重投、
  dedupe 幂等）；refresh_metrics 接心跳 + 五 lane span/Histogram +
  `/admin/execution/projection-status`（per-scope lag + 隔离清单）；投影按 run 聚批 +
  统计 SQL 化；组装迁 composition/kernel_runtime.py + import-linter 反向契约；kernel
  图显式系统身份；SandboxFactory 拆 attach-only/pooled；PUBLIC_CURSOR_SECRET 双密钥。

## 验收回归中发现并修复的缺陷（3 项，全部闭环）

1. **admission 提交 CreateRun v2**（K1 基线重置漏改提交侧，导致全部 Run 被拒
   INVALID_COMMAND_SCHEMA）——修为 v1；把钉死 v2 的存量测试改为绑定
   `command_registry.latest_version` 并实际 decide 校验 payload，杜绝"测试全绿产品全坏"。
2. **ask planner 丢弃水合的 decision payloads**（K3 收敛遗漏：`_plan_ask` 未传
   outcomes，而 ask 的 model.call 也带 decision_data digest）——触发 K1 的 fail-loud
   防线，Ask Run 永久卡死。outcomes 贯通 + 正反两条回归测试。
3. **单 planner 异常击穿整个决策批次**（上述异常曾让 decisions lane 每秒整批崩溃）——
   decision_worker 补 per-run 隔离：logger.exception + errors 计数 + disarm，坏 Run
   不再拖累批次，新事件可重新唤醒。

## 新增配置项汇总

`execution_activity_max_claim_attempts=5`、`execution_inbox_max_claim_attempts=10`、
`execution_{inbox,outbox}_retention_days=7`、`execution_{timer,activity}_retention_days=30`、
`execution_queue_purge_batch_size=500`、`execution_max_active_runs_per_scope=200`、
`shutdown_timeout_seconds` 默认 30→90、`PUBLIC_CURSOR_SECRET`（空则回退派生值）。
（均已同步 .env.example 注释。）

## 不兼容说明

按"新项目处理"决策：存量执行数据不迁移（本地/测试库销毁重建）；schema 变更直接落
初始 greenfield 迁移（新表 execution_poisoned_scopes、outbox payload 列、run 投影三列、
activity task decision_payload/claim_attempts 列、公共事件表 seq 主键、skills.allowed_tools
可空）；事件 schema 全部 v1 基线，此后演进受 EVOLUTION.md + CI 守卫强制。

## 遗留清零（2026-09-04 追加轮，全部闭环）

原六项遗留已全部实施并回归（单测 1849 / 集成 472 / lint + quality-check 绿 /
**验收 27/27 再次全绿**，累计 patch 16716 行、173 文件暂存）：

1. **SSE 事件驱动唤醒**：`WakeupBroadcastPort` 广播读（plain XREAD、每监听者自有游标），
   run_control 与 chat SSE 均接入，poll 间隔降为无提示兜底；**顺带修复一个附带缺陷**：
   chat SSE 此前复用内核 consumer group 会窃取唤醒提示——广播/竞争两种消费模式已在
   端口层显式分离并有"不偷组"回归测试。
2. **import-linter 豁免移除**：`composition/types.py` 的具体资源类型改 annotation-only
   （TYPE_CHECKING）+ 全局 `exclude_type_checking_imports = true`，第 5 条契约不再
   allow_indirect_imports，7 契约全 KEPT。
3. **inbox 计数拆分**：新增 `delivery_attempts` 列只计真实处理认领，死信上限按它判定
   （恢复"上限 10 = 10 次真实投递"），`claim_generation` 保持租约 fencing 语义；含
   "批量预认领不消耗投递预算"集成测试。**加码**：死信 inbox 行新增 30 天保留期清理
   （`EXECUTION_INBOX_DEAD_LETTER_RETENTION_DAYS`，0 禁用），不再无限增长。
4. **活动死信主动收敛**：死信转换同一事务向 inbox 写入
   `FailActivity(ACTIVITY_DEAD_LETTERED)`（timer_dispatcher 模式、确定性 command_id
   幂等），Run 下一个内核 tick 即收敛；活动超时 timer 降级为崩溃兜底。
5. **推理模型删除语义化**：删除仍被绑定的模型改为 400
   `inference.errors.modelInUse`（"请先解除绑定"），i18n 双语种 + contracts 登记，
   不再把 FK 违反漏成 500。
6. **UI 技能三态提示**：`SkillToolAccessBadge`——null 显示警示色"未限制工具"（带解释
   tooltip）、[] 显示"已禁用全部工具"、白名单展示不变；4 个 i18n 键 + 三态渲染测试。
