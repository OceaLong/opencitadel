# Skill

[English](skills.md)

Skill 是由用户显式选择、受 OwnerScope 约束的 Agent 执行 Profile。它不是自治路由器，
系统不会通过隐藏推荐逻辑自动选择 Skill。

## 契约

| 字段                     | 含义                                                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `system_prompt`、`body`  | 渲染进模型 Context 的指令                                                                                        |
| `resources`              | 为 Run 挂载的内联 Template、Script、Reference                                                                    |
| `allowed_tools`          | `null` 不追加工具名限制；`[]` 禁用全部工具；条目支持精确名、`*` Pattern 与 `a2a` Group                           |
| `mcp_server_refs`        | 可贡献允许工具的 MCP Server                                                                                      |
| `a2a_server_refs`        | 可贡献允许工具的 A2A Server                                                                                      |
| `recommended_model_id`   | 仅当调用方/Session 未选模型时采用                                                                                |
| `agent_params`           | Admission 冻结 `temperature_override`；存储的 `max_iterations`/`max_retries` 不覆盖 Run 的 Execution Policy 限制 |
| `override_base_rules`    | 存储的 Metadata；当前 Model-call 始终将 Skill 指令追加到 Platform Prompt                                         |
| `visibility`、Owner/Team | 资源授权边界                                                                                                     |

UI 或 API 可选提交 `skill_id`；Agent 可不选 Skill。Admission 在当前 OwnerScope 中解析
已选 Skill、验证 Enabled，并把其 Identity 与 Temperature Override 冻结进 Run Input。
Skill CRUD 校验推荐模型可见性与 Integration Reference；运行时再次授权所选资源。
不存在自动推荐 Endpoint 或 Feature Flag。

## 工具收窄

工具可用性取平台注册、Run Mode、Operator Scope、Skill Allowlist、Integration Reference 与
执行 Policy 的交集。Skill 只能收窄能力，不能授予调用方或平台原本没有的工具。

显式 MCP/A2A 工具 Pattern 必须有对应 Server Ref。已选 Skill 的空 Server Ref 不贡献
Server，`allowed_tools=null` 不取消该 Server 选择。Global Skill 只能引用 Global Integration。
重复、缺失、跨 Scope Reference 在 Mutation 时拒绝；Disabled Integration 从 Runtime Catalog
过滤。未声明 Policy 的工具采用最保守的 Effect、
Idempotency 与 Approval 分类。

## 执行

Model-call Activity 在冻结 OwnerScope 下重新加载所选 Skill，追加当前指令并应用冻结
Temperature。Catalog 同样重新加载当前工具限制。Skill 在准入后禁用时，执行继续，
不应用该 Skill 指令/限制，同时记录 Warning 与 Model-call Diagnostic Flag；Skill Content
并非不可变 Run Snapshot。Run 的 Execution Policy 与 Owner/Platform 检查仍生效。
Agent Tool Catalog 挂载 Skill Resource，只暴露当前获准工具。外部调用仍走正式持久 Activity 与
审批协议，Skill 文本不能绕过。

内置 Skill 作为产品 Template Seed。个人/团队 Skill 是通过同一验证的 CRUD 资源。Markdown
Import 先转换成 Native Skill；运行时只有一个 Native Model。
