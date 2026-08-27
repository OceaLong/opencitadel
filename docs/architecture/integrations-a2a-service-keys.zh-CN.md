# MCP、A2A 与 Service Key

[English](integrations-a2a-service-keys.md)

Integration 是 Owner Scope 资源。Activity 执行时，系统在 Run 冻结 OwnerScope 与已选 Skill
Reference 下解析它们。

## 出站 MCP 与 A2A

MCP Record 定义 Transport、Endpoint/Command、Header/Env、Enabled、Tool Policy、Visibility、
Owner/Team。A2A Record 定义 Endpoint、Enabled、Tool Policy、Visibility、Owner/Team。HTTP
Destination 通过 Outbound SSRF Validation。Stdio MCP 会在执行内核信任边界启动本地进程，
因此仅 Admin 可创建。

Agent Tool Catalog 只解析 Enabled、可访问 Record；带 Server Ref 的 Skill 进一步收窄。
Model 看到工具前先按 Mode/Policy Filter，Invocation 前再次 Resolve/Check。缺失或歧义 Tool Name
关闭失败。

MCP URL、Header 与 Environment Dictionary 中的 Secret 使用版本化加密信封。Response 做 Mask。
Masked/Blank Update 保留当前值；真实新值用 Active Key 加密。

## 入站 A2A

入站 `/api/a2a` 使用 Service API Key，在 Key Owner Authority 下提交正常 Agent Execution。
Service Key 只显示一次、Hash 存储、可撤销且有 Audit。Auditor Owner 的 Key 不能调用 A2A。
Service Key 不隐式选择 Team；Team Scope 交互 API 使用 Session Auth 与 `X-Workspace-Id`。

Remote Agent Call 是非确定 Activity。Request Identity、Timeout/Call-start、Result Reference 与
Failure 都持久化。Circuit/Open State 可阻止 Provider Call，但不能决定 Run Terminal State。

## 安全规则

- Global Integration 仅 Admin 创建；Private Integration 绑定一个 Personal/Team OwnerScope。
- Global Skill 只能引用 Global Integration。
- 每个 Integration Tool 都需明确 Policy；未声明工具采用保守且要求审批。
- Private Host/Port 需部署 Allowlist，Redirect 重新校验。
- Log、Public Event 与 Evidence 不包含 Credential 或原始 Authentication Header。
