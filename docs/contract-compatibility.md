# API/SSE 协议兼容策略

## ErrorEvent.code

- **后端**：`ErrorEvent.code` 可选，缺省为 `null`（旧事件经 `event_upgrader` 补全）
- **前端**：可读可忽略；优先用 `code` 驱动 UI，回退到 `error` 文案
- **兼容窗口**：至少 2 个 minor 版本

## Marketplace model_dependency

- **取值**：`none | optional | required`
- **缺省**：前端对缺失字段回落为 `optional`（catalog 接口保证全量下发）
- **FALLBACK_APPS**：离线兜底同样携带 `model_dependency`

## /api/llm/status

- 新增端点，不影响现有 `/api/status` 契约
- 响应 `Cache-Control: max-age=30`
