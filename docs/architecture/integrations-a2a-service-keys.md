# MCP, A2A, and Service Keys

[简体中文](integrations-a2a-service-keys.zh-CN.md)

Integrations are owner-scoped resources. They are resolved at Activity time
under the Run's frozen OwnerScope and selected Skill references.

## Outbound MCP and A2A

MCP records define transport, endpoint/command, headers/env, enabled state,
tool policies, visibility, and owner/team. A2A records define endpoint,
enabled state, tool policies, visibility, and owner/team. HTTP destinations
pass outbound SSRF validation. Stdio MCP is administrator-only because it
starts a local process in the execution-kernel trust boundary.

The Agent tool catalog resolves only enabled, accessible records. A Skill with
server refs narrows the set further. Tool definitions are filtered by mode and
policy before the model sees them, then resolved and checked again before
invocation. Missing or ambiguous tool names fail closed.

Secret values in MCP URLs, headers, and environment dictionaries use versioned
encrypted envelopes. Responses mask them. Masked/blank update fields retain
the current value; a real new value is encrypted with the active key.

## Inbound A2A

Inbound `/api/a2a` uses a service API key and submits normal Agent execution
under the key owner's authority. Service keys are shown once, stored as hashes,
revocable, and audited. An auditor-owned key cannot invoke A2A. Service keys do
not implicitly select a team; team-scoped interactive APIs use session auth and
`X-Workspace-Id`.

Remote Agent calls are nondeterministic Activities. Request identity,
timeout/call-start, result reference, and failure are durable. Circuit/open
state can block a provider call but cannot decide Run terminal state.

## Security rules

- Global integrations require admin creation; private integrations bind to one
  personal/team OwnerScope.
- Global Skills may reference only global integrations.
- Every integration tool needs an explicit policy; undeclared tools are
  conservative and approval-required.
- Private hosts/ports require deployment allowlisting and redirects are
  revalidated.
- Logs, public events, and evidence omit credentials and raw authentication
  headers.
