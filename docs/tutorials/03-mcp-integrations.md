[简体中文](03-mcp-integrations.zh-CN.md)

# Tutorial 3: Connect Internal Systems via MCP

Use the **Model Context Protocol (MCP)** to give your Agent tools that talk to internal APIs, databases, and SaaS — without custom code in the core platform.

## What is MCP in OpenCitadel?

MCP servers expose tools (e.g. `maps_geocode`, `read_url`) that the Agent calls like native tools. OpenCitadel supports:

- `stdio` — local process
- `sse` / `streamable_http` — remote HTTP servers

Configuration lives in `api/config.yaml` under `mcp_config.mcpServers` (a dictionary keyed by server name). You can also manage MCP servers from **Settings → Integrations** (`/settings/integrations`).

## Example: add a remote MCP server

Edit `api/config.yaml`:

```yaml
mcp_config:
  mcpServers:
    jina-mcp-server:
      transport: streamable_http
      url: https://mcp.jina.ai/sse
      enabled: true
```

Restart API and worker:

```bash
docker compose restart opencitadel-api opencitadel-worker
```

Tools appear with the `mcp_` prefix in the Agent tool list.

## Example: internal HTTP MCP gateway

For internal systems, run an MCP gateway inside your VPC:

```yaml
mcp_config:
  mcpServers:
    internal-crm:
      transport: streamable_http
      url: http://mcp-gateway.internal:8080/sse
      enabled: true
      headers:
        Authorization: "Bearer ${CRM_MCP_TOKEN}"
```

Store secrets in `.env` and reference via your deployment's secret injection.

## Template: stdio MCP (local script)

```yaml
mcp_config:
  mcpServers:
    company-tools:
      transport: stdio
      command: python
      args: ["/opt/mcp/company_tools_server.py"]
      enabled: true
```

Mount the script into the worker container or run MCP on a sidecar reachable via HTTP.

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

Open **Settings → Integrations** to view MCP and A2A server configuration without editing YAML directly (runtime changes persist when `USE_DB_APP_CONFIG=true`).

## Next

- [Architecture overview](../architecture/overview.md)
- [Contributing](../../.github/CONTRIBUTING.md)
