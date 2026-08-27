# 模型韧性

[English](model-resilience.md)

模型调用包含两个职责不同的可靠性层。

## Provider Call 层

`ResilientLLMClient` 负责一次 Activity 执行内部的有界尝试。它使用
`model_resilience.max_attempts_per_call` 与 `max_call_budget_seconds`，分类临时传输/
Provider 错误、记录熔断状态，并可选择符合条件的已配置备用模型。Quota 失败可直接切换到
下一个 Candidate；除非显式开启，否则不允许跨 Provider Fallback。

流式调用只允许在第一个输出 Chunk 之前重试或换 Provider。一旦开始输出，再次请求可能制造
可见重复，因此当前调用直接失败，由 Activity 协议处理。

熔断器只是运行保护，不是工作流状态。Circuit 打开会阻止 Provider 调用，但不会完成或取消 Run。

## Activity 层

执行内核持久化模型调用的输入 Reference、Invocation 身份、Claim Generation、Timeout、
call-start、Heartbeat 与 Result。Activity Retry 是工作流决策，必须出现在事件流中，不依赖进程
本地计数。过期 Worker 无法为旧 Claim Generation 提交完成。

两个层不能共享可变重试计数：Provider 尝试限定在一个 Invocation 内；Activity Retry 创建或
推进持久执行状态。

## 失败码

公开模型失败使用稳定码：`MODEL_NOT_CONFIGURED`、`MODEL_RATE_LIMITED`、`MODEL_TIMEOUT`、
`MODEL_QUOTA_EXCEEDED`、`MODEL_INVALID_REQUEST`、`MODEL_UNAVAILABLE` 与
`INFRASTRUCTURE_FAILED`。Provider 原始错误体不进入公开事件。运维日志和指标保留诊断类别与模型 ID，
但不记录凭据。

## 配置

活动 Execution Policy 的 `model_resilience` Section 控制：

- 有界尝试次数与墙钟预算；
- 熔断 Window、Threshold、Open TTL、Half-Open Probe Timeout；
- Fallback、Quota 行为与跨 Provider 权限；
- Circuit 打开时是否快速失败。

Fallback Candidate 仍必须通过 OwnerScope、启用状态、能力和 Provider Policy 检查。视觉请求不能
静默切换到不具备相应能力的模型。

## 验证

测试覆盖尝试边界、墙钟耗尽、熔断 Open/Half-Open、Quota Fallback、能力过滤、首 Chunk 后失败、
多模态文本降级，以及 Activity 重复/过期完成 Fencing。
