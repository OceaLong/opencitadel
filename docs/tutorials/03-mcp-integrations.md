[简体中文](03-mcp-integrations.zh-CN.md)

# Tutorial 3: Connect Internal Systems via MCP

Use the **Model Context Protocol (MCP)** to give your Agent tools that talk to internal APIs, databases, and SaaS — without custom code in the core platform.

## What is MCP in OpenCitadel?

MCP servers expose tools (e.g. `maps_geocode`, `read_url`) that the Agent calls like native tools. OpenCitadel supports:

- `stdio` — local process
- `sse` / `streamable_http` — remote HTTP servers

MCP servers are first-class, owner-scoped Integration resources. Create and manage them from **Settings → Integrations** or `/api/integrations/mcp-servers`; stable resource IDs are used by Skills and Automations.

## Example: add a remote MCP server

Open **Settings → Integrations → Add server** and submit:

```json
{
  "name": "jina-mcp-server",
  "transport": "streamable_http",
  "url": "https://mcp.jina.ai/sse",
  "enabled": true,
  "visibility": "private"
}
```

No service restart is required. The Integration list projects connection state and discovered tools; Agent tools use the `mcp_` prefix.

## Example: internal HTTP MCP gateway

For internal systems, run an MCP gateway inside your VPC:

```json
{
  "name": "internal-crm",
  "transport": "streamable_http",
  "url": "http://mcp-gateway.internal:8080/sse",
  "enabled": true,
  "visibility": "private",
  "headers": {"Authorization": "Bearer <token>"}
}
```

Credentials are encrypted at rest with the active API encryption key and masked on reads. Do not place Integration credentials in deployment variables or Runtime Policy.

## Template: stdio MCP (local script)

```json
{
  "name": "company-tools",
  "transport": "stdio",
  "command": "python",
  "args": ["/opt/mcp/company_tools_server.py"],
  "enabled": true,
  "visibility": "global"
}
```

Only administrators may create stdio or global resources. Mount the script into the execution-kernel container, or prefer an HTTP sidecar reachable from every kernel replica.

## Verify tools

1. Start a session
2. Ask: *What MCP tools do you have available?*
3. Invoke a tool: *Use the Jina reader to summarize https://example.com/docs*

## Security checklist

- [ ] MCP servers run inside the same trust zone as OpenCitadel
- [ ] Use service accounts with least privilege
- [ ] Audit tool calls via `audit_service` logs
- [ ] Disable unused MCP servers (`enabled: false`)

## Manage via UI

Open **Settings → Integrations** to manage MCP and A2A resources. Mutations persist immediately in PostgreSQL; connection health and discovered capabilities are read-side projections.

## Next

- [Architecture overview](../architecture/overview.md)
- [Contributing](../../.github/CONTRIBUTING.md)
