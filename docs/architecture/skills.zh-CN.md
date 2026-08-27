# Skill

[English](skills.md)

Skill 是由用户显式选择、受 OwnerScope 约束的 Agent 执行 Profile。它不是自治路由器，
系统不会通过隐藏推荐逻辑自动选择 Skill。

## 契约

| 字段 | 含义 |
| --- | --- |
| `system_prompt`、`body` | 渲染进模型 Context 的指令 |
| `resources` | 为 Run 挂载的内联 Template、Script、Reference |
| `allowed_tools` | 精确工具名 Allowlist；空列表不暴露 Skill 范围工具 |
| `mcp_server_refs` | 可贡献允许工具的 MCP Server |
| `a2a_server_refs` | 可贡献允许工具的 A2A Server |
| `recommended_model_id` | 仅当调用方/Session 未选模型时采用 |
| `agent_params` | Admission 时冻结的 `max_iterations`、`max_retries`、`temperature_override` |
| `override_base_rules` | 明确允许替换而非追加基础指令 |
| `visibility`、Owner/Team | 资源授权边界 |

UI 或 API 必须提交 `skill_id`。Admission 在当前 OwnerScope 中解析 Skill、验证 Enabled、
检查推荐模型和 Integration Reference，再把有效设置冻结进 Run Input。不存在自动推荐 Endpoint
或 Feature Flag。

## 工具收窄

工具可用性取平台注册、Run Mode、Operator Scope、Skill Allowlist、Integration Reference 与
执行 Policy 的交集。Skill 只能收窄能力，不能授予调用方或平台原本没有的工具。

MCP/A2A 工具名必须有对应 Server Ref。Global Skill 只能引用 Global Integration。重复、缺失、
跨 Scope 或 Disabled Reference 都会被拒绝。未声明 Policy 的工具采用最保守的 Effect、
Idempotency 与 Approval 分类。

## 执行

Model-call Activity 加载已准入 Skill、渲染 Active Instruction 并应用冻结的 Temperature。
Agent Tool Catalog 挂载 Skill Resource，只暴露已准入工具。外部调用仍走正式持久 Activity 与
审批协议，Skill 文本不能绕过。

内置 Skill 作为产品 Template Seed。个人/团队 Skill 是通过同一验证的 CRUD 资源。Markdown
Import 先转换成 Native Skill；运行时只有一个 Native Model。
