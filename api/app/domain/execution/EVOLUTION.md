# 执行内核事件/命令 Schema 演进守则

本文件是规范，不是文档摆设：`api/tests/app/domain/execution/test_schema_guards.py`
以此为基准做 CI 强制（golden hash、字段集快照、注册表自检）。

## 三条铁律

1. **禁止重定基线。** 一个事件/命令类型一旦在任何环境持久化过，它的 baseline
   注册（v1）永远不变。升级只能追加新版本 + upcaster，绝不允许把新形状直接
   注册成 v1（RunCreated 曾经这么干过，第一次真实升版就会炸掉重放）。
2. **同版本禁止改形状。** 任何字段增删改（包括默认值语义变化）都必须升版本。
   所有 payload 模型 `extra="forbid"`，这是有意的 fail-closed 策略：滚动发布
   时必须先发含 upcaster 的读端，再发写新版本的写端。
3. **加字段必升版本 + upcaster。** 事件的 upcaster 同时接收 public 与 internal
   两半 payload（`EventPayloads`），internal 结构（policy_snapshot、
   input_payload、decision_digest）与 public 走同一条演进管道，没有旁路。

## 配套机制（改动时同步检查）

- upcast 统一发生在 `PostgresEventStore` 读取边界（hash 校验之后），
  orchestrator 与全部 projector 消费同一结果；不要在任何消费方重新实现。
- `event_hash` 永远覆盖**存储原始形态**，upcast 不重算 hash。
- `RunState` 字段集变更必须 bump `RunAggregate.snapshot_serializer_version`
  （CI 有字段集快照测试守着）。
- 新增事件：同时更新 `_EVENT_SPECS`、`_EVOLVED_EVENT_TYPES`、evolve if 链，
  三者不一致会在聚合构造时（`_assert_registry_coverage`）直接抛错。
- 新增命令：注册 + `_decide_<Name>` 方法，缺一构造时抛错。
- 事件发射一律走 `RunAggregate._new_event`（写侧按 registry 校验），
  不要手写 `NewEvent(...)`。
