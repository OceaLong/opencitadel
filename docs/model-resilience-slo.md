# 模型域 SLO / 告警口径

## 平台域 (L0)

| 指标 | 目标 |
|------|------|
| `/api/status` 可用性 | ≥ 99.9% |
| P95 延迟 | < 500ms |

## 模型域 (L2/L3)

| 指标 | 说明 |
|------|------|
| 按 provider/model_id 成功率 | 来自 resilience_events |
| 429/5xx/timeout 比例 | 计入熔断窗口 |
| 熔断开路时长 | Redis `cb:state:*` TTL |
| fallback 命中率 | `fallback_success` 事件 |

## Embedding 域

| 指标 | 说明 |
|------|------|
| 索引任务成功率 | codebase ingest |
| 降级触发率 | `vector_degraded=true` |

## `/api/llm/status`

| 指标 | 目标 |
|------|------|
| 可用性 | > 99.5% |
| P95 | < 200ms（纯读聚合） |

阈值初值见 `AppConfig.model_resilience`，建议每周回顾调优。
