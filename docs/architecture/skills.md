# Skills

[简体中文](skills.zh-CN.md)

A Skill is an explicitly selected, owner-scoped Agent execution profile. It is
not an autonomous router and is never chosen by hidden recommendation logic.

## Contract

| Field                    | Meaning                                                                                                                          |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `system_prompt`, `body`  | Instructions rendered into model context                                                                                         |
| `resources`              | Inline templates, scripts, and references mounted for the Run                                                                    |
| `allowed_tools`          | `null` adds no tool-name restriction; `[]` disables all tools; entries support exact names, `*` patterns, and the `a2a` group    |
| `mcp_server_refs`        | MCP servers that may contribute allowed tools                                                                                    |
| `a2a_server_refs`        | A2A servers that may contribute allowed tools                                                                                    |
| `recommended_model_id`   | Model selected only when the caller/session did not select one                                                                   |
| `agent_params`           | Admission freezes `temperature_override`; stored `max_iterations`/`max_retries` do not replace the Run's Execution Policy limits |
| `override_base_rules`    | Stored metadata; the current model-call path always appends Skill instructions to the platform prompt                            |
| `visibility`, owner/team | Resource authorization boundary                                                                                                  |

The UI or API optionally supplies `skill_id`; an Agent can run without a Skill.
Admission resolves the selected Skill in the current OwnerScope, verifies that
it is enabled, and freezes its identity and temperature override into Run input.
Recommended-model visibility and integration references are validated by Skill
CRUD; runtime consumers authorize the selected resources again.
There is no endpoint or feature flag for automatic Skill recommendation.

## Tool narrowing

Tool availability is the intersection of platform registration, Run mode,
Operator scope, Skill allowlist, integration refs, and execution policy. A
Skill can narrow capability but cannot grant a tool that the caller or platform
does not already authorize.

Explicit MCP/A2A tool patterns require matching server refs. A selected Skill
with empty server refs contributes no servers; `allowed_tools=null` alone does
not waive this server selection. Global Skills may reference
only global integrations. Duplicate, missing, or foreign references are rejected at mutation; disabled
integrations are filtered from the runtime catalog. Tools without an explicit policy default to the most
conservative effect/idempotency/approval classification.

## Execution

The model-call Activity reloads the selected Skill under the frozen OwnerScope,
appends its current instructions, and applies the frozen temperature setting.
The catalog likewise reloads its current tool restrictions. Disabling the Skill
after admission continues without that Skill's instructions/restrictions, with
a warning and model-call diagnostic flag; Skill content is not an immutable Run
snapshot. The Run's Execution Policy and owner/platform checks still apply. The Agent tool catalog mounts
Skill resources and exposes only admitted tools. External calls still use the
normal durable Activity and approval protocol; Skill text cannot bypass it.

Built-in Skills are seeded as product templates. User and team Skills are CRUD
resources with the same validation. Markdown import converts one document to a
native Skill before validation; runtime execution uses one native model only.
