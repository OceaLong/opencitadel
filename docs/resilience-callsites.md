# ResilientLLMClient 调用点覆盖

## 经 ResilientLLMClient 包裹（chat LLM 域）

| 调用点 | 路径 |
|--------|------|
| Agent 主链路 | `TaskRunnerFactory._resolve_llm_and_config` → `create_resilient_llm` |
| MemoryExtractor | 继承 runner 注入的 `llm`（已是 ResilientLLMClient） |
| VisionGroundingTool | 继承 Agent 注入的 `llm` |
| Marketplace LLM | `_resolve_text_llm` / `_resolve_vision_llm` |

## 独立失败域（不接入 chat 熔断）

| 服务 | 错误码 |
|------|--------|
| AudioService (Whisper) | `AUDIO_TRANSCRIPTION_FAILED` |
| ImageGenerationService | `IMAGE_GENERATION_FAILED` |

## 显式 probe（不经韧性层）

| 调用点 | 说明 |
|--------|------|
| `LLMModelService._run_vision_probe` | 用户主动探测，使用原始 `LLMFactory.create` |
