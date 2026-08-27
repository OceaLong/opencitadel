# Skills

[简体中文](skills.zh-CN.md)

A Skill is an explicitly selected, owner-scoped Agent execution profile. It is
not an autonomous router and is never chosen by hidden recommendation logic.

## Contract

| Field | Meaning |
| --- | --- |
| `system_prompt`, `body` | Instructions rendered into model context |
| `resources` | Inline templates, scripts, and references mounted for the Run |
| `allowed_tools` | Exact tool-name allowlist; an empty list exposes no Skill-scoped tools |
| `mcp_server_refs` | MCP servers that may contribute allowed tools |
| `a2a_server_refs` | A2A servers that may contribute allowed tools |
| `recommended_model_id` | Model selected only when the caller/session did not select one |
| `agent_params` | `max_iterations`, `max_retries`, `temperature_override` frozen at admission |
| `override_base_rules` | Explicit permission to replace, rather than append to, base instructions |
| `visibility`, owner/team | Resource authorization boundary |

The UI or API supplies `skill_id`. Admission resolves the Skill in the current
OwnerScope, verifies that it is enabled, validates its recommended model and
integration references, then freezes effective settings into the Run input.
There is no endpoint or feature flag for automatic Skill recommendation.

## Tool narrowing

Tool availability is the intersection of platform registration, Run mode,
Operator scope, Skill allowlist, integration refs, and execution policy. A
Skill can narrow capability but cannot grant a tool that the caller or platform
does not already authorize.

MCP/A2A tool names require matching server refs. Global Skills may reference
only global integrations. Duplicate, missing, foreign, or disabled references
are rejected. Tools without an explicit policy default to the most
conservative effect/idempotency/approval classification.

## Execution

The model-call Activity loads the admitted Skill, renders active instructions,
and applies the frozen temperature setting. The Agent tool catalog mounts
Skill resources and exposes only admitted tools. External calls still use the
normal durable Activity and approval protocol; Skill text cannot bypass it.

Built-in Skills are seeded as product templates. User and team Skills are CRUD
resources with the same validation. Markdown import converts one document to a
native Skill before validation; runtime execution uses one native model only.
