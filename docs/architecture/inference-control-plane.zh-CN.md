# 推理控制面

[English](inference-control-plane.md)

OpenCitadel 使用一套统一推理控制面承载 Chat、Embedding 与 Rerank，由三类显式资源组成：

- Endpoint 持有 Provider、Base URL、加密 Credential、Visibility 与 Owner Scope；
- Model 隶属于一个 Endpoint，持有 Provider Model Name、Kind、Settings、Price 与能力声明；
- Binding 为 `chat`、`embedding` 或 `rerank` 用途选择一个有效 Model。

系统不存在环境变量 Key 回退、隐式默认模型或独立向量 Credential 路径。消费者必须解析用途
Binding；无法解析时用稳定错误键 Fail Closed。

## Scope 与解析

Endpoint、Model、Binding 可以是 Global 或 Personal/Team Scope。只有管理员能修改 Global
Resource。Workspace Binding 覆盖可见的 Global Binding；删除 Workspace Override 后恢复继承。
被引用的 Model 与 Endpoint 必须同时在当前 Owner Scope 可见。

解析过程强制校验 Purpose/Kind：`chat` 与 `rerank` 使用 Chat Model；`embedding` 使用平台维度
固定为 1536 的 Embedding Model。没有显式 Rerank Binding 时，Rerank 可以解析 Chat Binding。
资源缺失、不可访问或 Kind 不匹配时，绝不会回退到无关 Provider。

## Provider 与 Credential

Provider Registry 是 Provider/Kind 支持矩阵的唯一事实来源，覆盖 OpenAI、Azure OpenAI、
Ollama、Anthropic 与 Gemini。写入前即验证组合。Ollama 可以不配置 Credential；要求
Credential 的 Provider 缺失时 Fail Closed。

Credential 只以版本化 `fernet_v2` 信封存储。API 仅返回 `credential_configured`，不返回
明文；空白更新保留已有 Credential。Active Encryption Key 加密新写入，显式 Previous-Key
Ring 支持计划内轮换。

## Capability 与消费者

`GET /api/capabilities` 投影当前 Owner Scope 的 Chat、Embedding、Rerank、A2A、Patrol 与
Patrol Remediation 可用性。UI 与服务端 Admission 使用相同状态：`available`、`degraded`、
`not_configured`、`disabled` 或 `denied`，并共享稳定 Reason Key。

Chat 执行、Codebase/Knowledge/Memory 向量化与 Rerank 均在调用时解析控制面。活动 Execution Policy
可以分别禁用向量消费者，但已启用的消费者不会读取独立 API Key 或 Base URL。能力缺失时，
UI 引导用户前往 **设置 → 推理**。

## API 与运维

稳定 API 位于 `/api/inference`：

- `/endpoints` 管理连接信息与 Credential 所有权；
- `/models` 管理有类型的 Chat/Embedding Model 与 Probe；
- `/bindings` 管理 Global/Workspace 用途选择；
- `/status` 报告当前 Owner Scope 的有效控制面状态。

Demo Seed 需在 API 容器启动前设置 `DEMO_INFERENCE_BASE_URL`、
`DEMO_INFERENCE_CREDENTIAL`、`DEMO_INFERENCE_MODEL` 与
`DEMO_INFERENCE_PROVIDER`。生产 Credential 应通过已认证 API/UI 与受管 Secret 流程创建，
不能写入版本库配置。

推理调用作为持久 Activity 执行。Run Input 记录选中的 Model Identity 与稳定失败分类；
Credential 和原始私有输入不会进入 Public Event。重试与回退策略见
[模型韧性](model-resilience.zh-CN.md)。
