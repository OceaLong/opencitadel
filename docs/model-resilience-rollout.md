# 模型隔离治理 — 运行手册

## 灰度顺序

1. **只观测**：部署 `/api/llm/status` 与 resilience 指标，不开启 fallback
2. **分级错误码**：前后端识别 `ErrorEvent.code`
3. **熔断**：`model_resilience.enabled=true`，`fallback_enabled=false`
4. **Fallback**（可选）：`fallback_enabled=true`，仍保持 `allow_cross_provider_fallback=false`

## Kill-switch

- `model_resilience.enabled=false` — 关闭熔断与 ResilientLLMClient 快速失败
- `model_resilience.fallback_enabled=false` — 关闭同 Provider fallback
- `feature_flags.enable_agent_features=false` — 关闭 Agent/A2A 入口

## DLQ 重放手册

1. 仅重放 `error_code` 以 `MODEL_` 开头的条目
2. 确认对应 `model_id` 熔断状态为 `closed`
3. 批次 ≤ `dlq_replay_batch_size`，间隔 ≥ `dlq_replay_interval_seconds`
4. 模型再次 open 时暂停重放

## A2A 固定错误

模型不可用时 JSON-RPC error code `-32001`，message：`模型服务暂不可用（熔断开路），请稍后重试`
