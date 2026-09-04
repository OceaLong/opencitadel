# 执行内核彻底改造 · 总体 Spec（2026-09-03）

依据：`specs/2026-09-03-kernel-architecture-audit.md`（1 P0 / 15 P1 / 18 P2 / 建议级若干）。
用户决策：**一次性全做，按新项目处理，不考虑向后兼容，彻底改造。**

## 0. 全局约束（沿用项目惯例）

- 所有改动只 `git add` 暂存，不 commit；specs/ 不入库。
- 每完成一个 plan 立即 `git diff HEAD > <仓库外>/kernel-overhaul.patch` 备份。
- 验收基线：api 单测全绿、Postgres 集成测试全绿、ui 全绿、`make lint` + `make quality-check`、验收套件 27/27。每个 plan 收尾都要回归。
- 不兼容含义：**存量数据不迁移**。本地/测试库销毁重建（`docker compose --project-name opencitadel --profile local down -v` 后重启）；执行内核相关表结构直接改初始迁移/模型定义，不写数据迁移脚本；事件 schema 全部重定为干净 v1 基线。

## 1. 目标与非目标

**目标**：修复审计全部 P0/P1/P2 与建议项，并把内核的四个结构性弱点根治：
1. 可用性击穿链（饿死/进程崩/发版杀单/重试风暴/工具异常击穿）；
2. schema 演进管道半吊子；
3. 扩展面四种注册模式并存、双清单漂移、字符串耦合；
4. 观测闭环断裂 + 投影单点放大。

**非目标**：不改产品功能语义（Run 家族、审批流程、巡检语义不变）；不引入新中间件（仍是 Postgres + Redis）；不做 LISTEN/NOTIFY 全事件驱动重写（保留轮询骨架，做脏标记 + consumer group 优化）；model.call 流式进度只做最小接线，完整流式 UI 属后续产品项。

## 2. 十四条约束性架构决策（各 plan 必须遵守）

- **D1 事件基线重置**：所有事件/命令类型统一注册为 v1 baseline，删除 RunCreated/CreateRun 的 v2 ternary 与 `_validate_event` 硬编码。同时立演进守则（写入 `api/app/domain/execution/EVOLUTION.md` 并被 CI 守卫引用）：上线后禁止重定基线；同版本禁止改 shape；加字段必升版本 + upcaster。
- **D2 统一 upcast 管道**：upcast 下沉到 EventStore 读取边界（`replay`/流读取统一执行），orchestrator 与所有 projector 共享同一条演进管道；`internal_payload` 改为 `{schema_version, data}` 信封并纳入 registry 管理；聚合启动自检（`_EVENT_PAYLOADS` 键 ⊆ registry 且发射版本==latest）+ `append` 前对 NewEvent 做注册校验。
- **D3 遥测出流**：`ActivityProgressed` 移出聚合事件流 → 新 append-only 表（无 hash 链、无 scope advisory 锁）；`RunState.activity_results` 只存 ref+digest，decision_data 落独立存储；进度序列约束移到新表的 (activity_id, generation, sequence) 唯一键。
- **D4 决策就绪模型**：run 投影表新增 `status` / `wait_reason` / `active_activity_count` / `decision_due_at` 列，`load_ready` 在 SQL 层只取可决策行；投影器在活动结算、审批落定、timer 到期等事件时置位。这是 P0 的根治方案。
- **D5 worker 韧性对称**：活动面获得与控制面等价的 lane 隔离；`gather(..., return_exceptions=True)` 逐项收集；毒丸稳定异常走 defer+退避；优雅关停三段式（停新 claim → 在 shutdown_timeout 内 await 在途 handler → 超时才 cancel）。
- **D6 重试与退避统一**：Run 级重试经 timer 调度指数退避（基数按 retry_generation）；inbox 增加 attempts 上限 + dead_letter；orchestrator 乐观冲突重试加抖动退避。
- **D7 队列生命周期**：inbox/outbox/timer/activity 完成行由 job_scheduler leader tick 分批清理（保留期可配）；同一 tick 挂 dead_letter 告警计数。
- **D8 工具执行契约 v2**：ToolCall handler 设异常边界，工具级异常（参数错误、CapabilityDenied、工具内部 ValueError）一律归一化为 `ToolResult(success=False)` 作为 tool 消息喂回模型循环，不再击穿为 Run 失败；入参按工具 JSON schema 前置校验；只有基础设施异常（DB/超时/取消）才升格为 activity 失败。
- **D9 目录快照一致性**：`definitions()` 产出的工具目录快照（工具名单 + policy 摘要 + skill/MCP 指纹）随 model.call 结果落入 decision_data；tool.call 按快照解析执行，目录漂移归一为可反馈给模型的 tool error，不再整 Run 失败。
- **D10 声明式注册收敛**：以 `inference/registry.py` 的 ProviderSpec 为范本建三个声明式注册表——ToolSpec（唯一工具装配点，删除 `tool_registry.py` 双清单）、ActivitySpec（activity_type 常量单源）、DecisionPlannerSpec（family→planner dict，替换 if/elif 链）；内核启动期断言"决策侧使用的 activity 类型集合 ⊆ 注册集合"。Vision 工具正式接入生产目录。
- **D11 信任边界统一**：MCP/A2A 的远端 description/inputSchema/返回内容套用 browser 同款 UNTRUSTED 包裹与尺寸上限；skill 白名单语义显式化（None=不限制，[]=禁全部，导入路径必须显式选择）；a2a 组标识常量单源；frontmatter 换真 YAML 解析。
- **D12 投影韧性**：per-scope try/except + 连续失败隔离（poisoned_scopes 表，复用 poisoned_runs 模式）+ 指标告警；rebuild 获得 admin CLI 入口且窗口内 record 缺失返回可重试信号（worker defer 而非永久失败）；审批通知改走 outbox（持久化、带 dedupe_key）。
- **D13 观测闭环**：`refresh_metrics` 接入控制面心跳周期调用并加行为级测试；四条 lane（inbox/projector/outbox/timer）补 OTEL span 与耗时直方图；SSE 空转超时发显式 `stream_timeout` 事件；scope 级投影 lag 暴露到 status 端点；快照失效指标分 schema_drift/corruption 标签。
- **D14 边界修缮**：`build_execution_kernel_runtime` 迁入 `app/composition/`；import-linter 补契约（infrastructure 禁止 import composition/execution_kernel*，去掉 interfaces 契约的 allow_indirect_imports 豁免并处理违规点）；kernel 装配显式传系统身份（禁止 authorization=None 回退进入 kernel 图）；SandboxFactory 拆 attach-only（API 进程）与 pooled（kernel 进程）两个窄接口；死代码全清（api/app/kernel/ 死包、FormalProjector 门面、PatrolTool、MCPTool.register_schema、CapabilityPolicy.for_child、过时注释）。

## 3. 计划划分与执行顺序

按子系统分四个 plan（同一文件不跨 plan 改，冲突面最小化）：

| Plan | 文件 | 覆盖审计项 | 依赖 |
|---|---|---|---|
| K1 领域模型与事件流 | plan-k1-domain-events.md | P1-5/6/7、P2-1/2/3/4、D1/D2/D3、死包清理 | 无（最先做，改动波及最广） |
| K2 运行时循环 | plan-k2-runtime-loops.md | P0-1、P1-1/2/3/4、P2-5/6/7、建议级（admission 闸门、幂等复核、冲突退避）、D4/D5/D6/D7 | K1（新表/新事件形状） |
| K3 可插拔扩展面 | plan-k3-plugin-surface.md | P1-8/9/10/11/12、P2-8/9/10/11/12、D8/D9/D10/D11 | K1（decision_data 形状）；与 K2 可并行 |
| K4 投影/观测/边界 | plan-k4-projection-observability.md | P1-13/14/15、P2-13~18、建议级（游标密钥、SSE 事件化）、D12/D13/D14 | K1/K2（投影列、outbox 通知） |

执行顺序：K1 → K2 与 K3 并行 → K4 → 全量回归（单测/集成/验收 27/27）→ patch 备份。每个 plan 内部先改代码后补测试守卫，plan 收尾跑该域测试 + `make lint`。

## 4. 全局验收标准

1. 既有测试全绿（允许因不兼容重构而**改写**测试断言，不允许删除覆盖）；验收套件 27/27。
2. 新增 CI 守卫全部落地并通过：canonical hash golden-file、RunState 变更强制 serializer_version bump、事件注册自检、决策↔活动类型交叉断言、metrics 必被周期刷新的行为测试、EVOLUTION.md 守则引用测试。
3. 场景验证（本地栈实测）：
   - 100 个挂起审批 Run 存在时，新建 Run 仍能在一个决策周期内启动（P0 根治证明）；
   - kernel 进程 SIGTERM 时在途模型调用正常完成、Run 不判 FAILED（排水证明）；
   - 模型传坏参数调用工具时，Run 继续运行且模型收到错误 tool 消息（D8 证明）；
   - 人为写坏一个 scope 的投影行后，其他 scope 投影照常推进且指标/日志有隔离记录（D12 证明）；
   - Prometheus 端点上 inbox 深度/投影 lag Gauge 有非零真实值（D13 证明）。
4. `grep` 级断言：`tool_registry.build_default_tools`、`api/app/kernel/`、`FormalProjector` 门面等死代码零残留；决策/活动类型字符串字面量只在常量模块出现。
