# Checkpoints, HITL Gates, and Web Operator

[简体中文](checkpoints-and-hitl.zh-CN.md)

This document covers human-in-the-loop (HITL) gate contracts, session checkpoints (including browser profile snapshots), and Web Operator ownership (`operator_scope`).

## HITL overview

```mermaid
flowchart TD
  Agent["Agent flow"] --> Gate{"pending_phase?"}
  Gate -->|clarify| Clarify["ClarifyAgent question"]
  Gate -->|plan_approval| Plan["Plan + risk tools"]
  Gate -->|tool_approval| Tool["Per-call tool gate"]
  Gate -->|takeover| VNC["Browser VNC takeover"]
  Clarify --> Resume["User resume message"]
  Plan --> Resume
  Tool --> Resume
  VNC --> Resume
  Resume --> Agent
```

Sessions store gate state in `pending_metadata` (JSONB) alongside `pending_phase`.

### Phases

| `pending_phase` | Purpose |
|-----------------|---------|
| `clarify` | Pre-plan clarification |
| `plan_approval` | Plan + task-level tool authorization |
| `tool_approval` | Call-by-call tool gate |
| `takeover` | Browser user takeover |

### Metadata shapes

- **plan_approval**: `{ plan, edited_plan?, risk_tools, approved_tools }`
- **tool_approval**: `{ pending_tool_call: { tool_call_id, tool_name, args }, approved_tools? }`
- **takeover**: `{ takeover: { started_at, timeout_minutes } }`

### Resume prefixes

User resume messages use prefixes: `approve`, `approve_with_edits`, `approve_same`, `reject: feedback`, `takeover`, `skip`.

Unknown or empty resume input resolves to action `unknown` and keeps the gate waiting (returns `WaitEvent`).

### Plan approval resume

After approval, the flow restores `plan` / `edited_plan` from `pending_metadata` and **does not** overwrite it with `session.get_latest_plan()`.

### Tool approval resume

On approve/reject, the agent injects the tool result into memory and continues the ReAct loop via `continue_tool_iteration_loop`.

## Shared tool-governance contract

Every registered tool exposes a `ToolExecutionPolicy`; governance uses the
descriptor both when schemas are shown to the model and immediately before
execution.

| Field | Values | Meaning |
|-------|--------|---------|
| `capability` | `message`, `knowledge_read`, `code_read`, `integration_read`, `web_read`, `generation`, `execution`, `unknown` | Mode-scoped capability |
| `effect` | `read_only`, `workspace_write`, `external_write`, `interactive` | Side-effect class |
| `idempotency` | `safe`, `idempotent_with_key`, `non_idempotent`, `unknown` | Automatic-retry boundary |
| `approval` | `never`, `policy`, `always` | Approval decision source |
| `concurrency_group` | string, default `none` | Serialization lane |

A missing or invalid declaration becomes the conservative policy:
`capability=unknown`, `effect=interactive`, `idempotency=unknown`,
`approval=always`, and `concurrency_group=unknown`.

Ask mode permits only read-only `message`, `knowledge_read`, `code_read`, and
explicitly administrator-classified `integration_read` descriptors. The same
policy is rechecked at invocation, including MCP/A2A calls. Child agents inherit
the parent policy and may only narrow it. Requests requiring creation,
modification, deletion, execution, external writes, or delegation remain
successful Q&A turns with zero side effects and direct the user to switch to
Agent mode.

### Persistent approval batches

The authoritative tool gate is a persisted batch, not the legacy single
`pending_tool_call`. The JSON inside the normal API `data` envelope has this
shape:

```json
{
  "id": "batch-id",
  "session_id": "session-id",
  "status": "pending",
  "expires_at": "2026-07-29T10:15:00Z",
  "created_at": "2026-07-29T10:00:00Z",
  "decided_at": null,
  "calls": [
    {
      "id": "approval-call-id",
      "batch_id": "batch-id",
      "tool_call_id": "model-call-id",
      "ordinal": 0,
      "tool_name": "browser_click",
      "normalized_args": {"target": "submit"},
      "args_hash": "sha256",
      "capability": "execution",
      "effect": "interactive",
      "idempotency": "non_idempotent",
      "approval": "always",
      "concurrency_group": "browser",
      "status": "pending",
      "decided_by": null,
      "decided_at": null
    }
  ]
}
```

Batch status is `pending`, `approved`, `rejected`, `expired`, or `consumed`.
The transient first-consumer marker `execution_claimed` is deliberately
excluded from JSON and persistence.

The complete model-produced call list is normalized, authorized, and persisted
in ordinal order before execution. Calls not requiring a human decision are
recorded as policy-approved, but if any sibling remains pending the executor
runs no call at all—including read-only calls—and therefore no effectful call
can run before approval.

| Route | Contract |
|-------|----------|
| `GET /api/sessions/{session_id}/tool-approval-batch` | Return the current owner-scoped pending batch |
| `POST /api/sessions/{session_id}/tool-approval-batches/{batch_id}/decision` | Body: `{"action":"approve|approve_same|reject","tool_call_ids":[...]?}` |

An explicit `tool_call_ids` selection supports partial decisions; omitted IDs
do not expand an earlier partial decision. A partial batch stays waiting.
`approve_same` grants a session allowance only for calls that were pending and
newly approved by that action.

Resume loads the persisted batch by ID, rejects foreign, missing, expired,
rejected, partial, or consumed batches, and revalidates ownership,
authorization, capability, normalized argument hash, signature, and the full
policy snapshot. Only a fully approved, unexpired batch may atomically move to
`consumed`; only the transaction that receives the transient execution claim
may invoke it. Repeated resume is non-executing and therefore idempotent.
`safe` calls may retry bounded transient failures;
`idempotent_with_key` may retry only when the schema and callable accept the
same stable key; `non_idempotent` and `unknown` run once.

### Takeover resume

User sends `takeover` or `skip`; pending phase is cleared, `roll_back` injects the user message into the pending `message_ask_user` tool call, then the ReAct loop continues.

Configure defaults in `AppConfig.hitl` (`tool_gate_call_level_enabled`, `tool_gate_risk_list`, etc.).

## Checkpoints

```mermaid
flowchart LR
  User["User rollback"] --> API["POST /sessions/{id}/checkpoints/{id}/restore"]
  API --> CP["CheckpointService"]
  CP --> Mem["Restore memory + files + session_state"]
  CP --> Browser["Restore browser profile tarball"]
  Browser --> Sandbox["Active sandbox via CDP"]
```

Each checkpoint captures:

| Component | Storage |
|-----------|---------|
| Agent memory snapshot | PostgreSQL / session state |
| Workspace files | Object storage keys under session scope |
| Session state | DB row metadata |
| Browser profile | Optional `browser_snapshot_key` → object storage (`checkpoints/{session_id}/{checkpoint_id}_browser.tgz`) |

Browser snapshots are captured only when:

1. The session has `operator_scope` set (Web Operator flow), and
2. An active sandbox exists at checkpoint creation time.

Rollback restores file and memory state, then re-imports the browser profile tarball into the sandbox when `browser_snapshot_key` is present.

## Web Operator and operator_scope

Web Operator is a **built-in Skill** (`web-operator`) for browser automation inside sandboxes—not a Marketplace app.

| `operator_scope` | Meaning |
|------------------|---------|
| `owned` | Target is enterprise-owned or self-hosted system |
| `third_party_saas` | Target is third-party SaaS; requires explicit user declaration |

Flow:

1. User starts a Web Operator session or selects scope at session creation.
2. UI shows ownership declaration dialog for third-party targets.
3. API persists `sessions.operator_scope` and writes an audit log (`operator_scope_declared`).
4. Checkpoints may include browser profile snapshots for rollback within the same scope.

> Third-party scope declaration creates an audit trail; it does **not** waive legal or contractual obligations with external services.

## Artifact delivery (related)

Agent-delivered artifacts follow a separate lifecycle:

1. `artifact_write` uploads content to object storage (`artifacts/{session_id}/{artifact_id}/v{n}.ext`); DB stores metadata only.
2. `ArtifactEvent` streams to the session workbench.
3. `artifact_finalize` marks `status=final`.
4. `POST /artifacts/{id}/share` creates a token → public `/share/artifact/{token}`.

HTML artifacts are sanitized server-side; cross-scope access returns 404. See [Security model — Artifacts](security-model.md#artifacts-and-trusted-delivery).

## API routes

| Route | Purpose |
|-------|---------|
| `POST /api/sessions` | Optional `operator_scope` on create |
| `POST /api/sessions/{id}/checkpoints` | Create checkpoint |
| `POST /api/sessions/{id}/checkpoints/{id}/restore` | Restore checkpoint |
| `GET /api/sessions/{id}/vnc` | WebSocket VNC proxy |

## Related docs

- [Security model](security-model.md)
- [Events — wait / plan / tool](events.md)
