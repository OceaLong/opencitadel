# MyManus 文档索引

本文档是 `docs/` 目录的导航入口，说明各设计文档的权威范围与推荐阅读顺序。

## 推荐阅读顺序

1. [系统架构](architecture.md)：先理解 API、Worker、Redis Streams、Postgres、沙箱和部署边界。
2. [事件系统](events.md)：再理解领域事件、SSE 投影、持久化与 replay。
3. [配置来源治理](config-source-governance.md)：确认行为配置、密钥连接和部署种子的来源边界。
4. [模型韧性设计](model-resilience.md)：理解模型不可用时的熔断、fallback、SLO 和运行手册。
5. [API/SSE 协议兼容策略](contract-compatibility.md)：查看前后端契约兼容窗口。
6. [Codebase 向量降级与重新索引](codebase-reindex.md)：查看 embedding 不可用时的降级与恢复路径。
7. [架构演进指南](architecture-evolution.md)：查看从单机 Compose 到 K8s / 外置沙箱的演进路径。

## 文档清单

| 文档 | 权威范围 |
|------|----------|
| [系统架构](architecture.md) | 系统总体架构、进程职责、任务执行状态、沙箱生命周期、依赖注入与部署形态 |
| [事件系统](events.md) | 领域事件、SSE 线上契约、事件投影、持久化与分页重放 |
| [配置来源治理](config-source-governance.md) | `AppConfig`、`config.yaml` / Helm `appConfig`、`Settings` 环境变量的边界 |
| [模型韧性设计](model-resilience.md) | 模型隔离、Redis 熔断、fallback 策略、SLO、灰度、Kill-switch、DLQ 手册 |
| [API/SSE 协议兼容策略](contract-compatibility.md) | `ErrorEvent.code`、Marketplace `model_dependency`、`/api/llm/status` 的兼容约束 |
| [Codebase 向量降级与重新索引](codebase-reindex.md) | Codebase ingest 在 embedding 不可用时的降级、UI 提示与重新索引 |
| [架构演进指南](architecture-evolution.md) | 单机稳定化、DB / Redis 外置、K8s HPA、沙箱外置与全托管演进 |

## 维护规则

- 每份文档只维护一个主题的权威说明，避免在多个文件重复描述同一策略。
- 行为配置变化需同步 [配置来源治理](config-source-governance.md) 中的清单。
- API / SSE 契约变化需同步 [API/SSE 协议兼容策略](contract-compatibility.md)。
- 新增功能设计应在对应主题文档中补充 Mermaid 架构图、状态图或流程图。
