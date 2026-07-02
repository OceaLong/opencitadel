[English](contract-compatibility.md)

# API/SSE 协议兼容策略

本文档是 OpenCitadel API、SSE 事件与前后端兼容窗口的权威说明。

## ErrorEvent.code

| 侧 | 策略 |
|----|------|
| 后端 | `ErrorEvent.code` 可选，缺省为 `null`；旧事件经 `event_upgrader` 补全 |
| 前端 | 可读可忽略；优先用 `code` 驱动 UI，回退到 `error` 文案 |
| 兼容窗口 | 至少 2 个 minor 版本 |

## Marketplace model_dependency

| 项 | 策略 |
|----|------|
| 取值 | `none | optional | required` |
| 缺省 | 前端对缺失字段回落为 `optional`；catalog 接口保证全量下发 |
| `FALLBACK_APPS` | 离线兜底同样携带 `model_dependency` |

## /api/llm/status

| 项 | 策略 |
|----|------|
| 契约关系 | 新增端点，不影响现有 `/api/status` 契约 |
| 缓存 | 响应 `Cache-Control: max-age=30` |

## 相关文档

- [事件系统](events.zh-CN.md)
- [模型韧性设计](model-resilience.zh-CN.md)
- [配置来源治理](config-source-governance.zh-CN.md)
