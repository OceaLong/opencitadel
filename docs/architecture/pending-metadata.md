# Pending metadata gate contract

Sessions store gate state in `pending_metadata` (JSONB) alongside `pending_phase`.

## Phases

| pending_phase | Purpose |
|---------------|---------|
| `clarify` | Pre-plan clarification |
| `plan_approval` | Plan + task-level tool authorization |
| `tool_approval` | Call-by-call tool gate |
| `takeover` | Browser user takeover |

## Metadata shapes

- **plan_approval**: `{ plan, edited_plan?, risk_tools, approved_tools }`
- **tool_approval**: `{ pending_tool_call: { tool_call_id, tool_name, args }, approved_tools? }`
- **takeover**: `{ takeover: { started_at, timeout_minutes } }`

Resume messages use prefixes: `approve`, `approve_with_edits`, `approve_same`, `reject: feedback`, `takeover`, `skip`.

Unknown or empty resume input resolves to action `unknown` and keeps the gate waiting (returns `WaitEvent`).

## Plan approval resume

After approval, the flow restores `plan` / `edited_plan` from `pending_metadata` and **does not** overwrite it with `session.get_latest_plan()`.

## Tool approval resume

On approve/reject, the agent injects the tool result into memory and continues the ReAct loop via `continue_tool_iteration_loop`.

## Takeover resume

User sends `takeover` or `skip`; pending phase is cleared, `roll_back` injects the user message into the pending `message_ask_user` tool call, then the ReAct loop continues.
