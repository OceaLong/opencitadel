# 模型隔离 P0 最小切片

## 目标

模型全部不可用时，平台仍满足：

- 配置可改（probe 解耦）
- 健康可见（`/api/status` + `/api/llm/status`）
- Room / 问卷 / 文件 / Marketplace 目录可用
- Agent / Marketplace LLM 以明确原因快速失败，不堆积资源

## P0 范围

- DB 配置冷启动种子
- ModelResilienceConfig + feature_flags
- probe 解耦
- 健康面拆分 + `/api/llm/status` 阶段一
- 分级 ErrorEvent.code
- 熔断错误分类 + Worker 快速失败 + reconcile 熔断联动
- 前端区分模型异常与系统宕机（核心）

## 非 P0（增强）

- 跨 Provider fallback
- DLQ 自动重放
- 完整容器拆分
- UI Badge 全量（已部分落地）
