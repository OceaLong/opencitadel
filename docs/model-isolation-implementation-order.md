# 模型隔离落地顺序

## 短期（已落地 / 低风险）

DB 配置迁移、AppConfig schema、probe 解耦、健康拆分、`/api/llm/status`、ErrorEvent.code、Marketplace catalog `model_dependency`、Marketplace 懒解析 LLM。

## 中期（核心韧性）

Redis 熔断 + half-open Lua、ResilientLLMClient、DLQ error_code、Worker 快速失败、reconcile 熔断联动、Embedding ingest 降级、A2A 入口治理。

## 长期（结构隔离）

feature_flags 路由分组、Worker runner registry、UI 全量可视化、Codebase reindex UI。

## 回归重点

`test_model_error_fixes.py`、`test_reconcile.py`、`test_status_routes.py`、新增 `test_circuit_breaker.py`、`test_event_upgrader.py`、`test_marketplace_catalog.py`。
