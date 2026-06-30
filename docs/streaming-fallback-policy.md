# 流式生命周期 Fallback / 重试边界

## 策略

| 阶段 | 行为 |
|------|------|
| 首 token / 首 delta **发出前** | 可走 ResilientLLMClient 瞬态重试与同 Provider 能力匹配 fallback |
| 已 emit 任意 delta **之后** | **禁止** mid-stream 换模型；以 `ErrorEvent.code=MODEL_*` 终止 |
| 非流式 `invoke` | 不受 mid-stream 限制，可重试/fallback |

## 实现

- `ResilientLLMClient.streaming_started` 在首个 chunk yield 后置位
- `stream_invoke` 在 `streaming_started=True` 后遇错直接抛 `ModelUnavailableError`，不切换候选模型
- OpenAI 路径已移除 `with_llm_retry`，重试权威在 ResilientLLMClient
