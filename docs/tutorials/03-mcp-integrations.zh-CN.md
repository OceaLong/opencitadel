[English](03-mcp-integrations.md)

# 教程 3：通过 MCP 连接内部系统

使用 **Model Context Protocol (MCP)** 为 Agent 提供与内部 API、数据库和 SaaS 交互的工具 —— 无需在核心平台中编写自定义代码。

## OpenCitadel 中的 MCP 是什么？

MCP 服务器暴露工具（如 `maps_geocode`、`read_url`），Agent 可以像调用原生工具一样调用它们。OpenCitadel 支持：

- `stdio` — 本地进程
- `sse` / `streamable_http` — 远程 HTTP 服务器

配置位于 `api/config.yaml` 的 `mcp_servers` 下。

## 示例：添加远程 MCP 服务器

编辑 `api/config.yaml`：

```yaml
mcp_servers:
  - name: jina-reader
    transport: streamable_http
    url: https://mcp.jina.ai/sse
    enabled: true
```

重启 API 和 worker：

```bash
docker compose restart opencitadel-api opencitadel-worker
```

工具会以 `mcp_` 前缀出现在 Agent 工具列表中。

## 示例：内部 HTTP MCP 网关

对于内部系统，在 VPC 内运行 MCP 网关：

```yaml
mcp_servers:
  - name: internal-crm
    transport: streamable_http
    url: http://mcp-gateway.internal:8080/sse
    enabled: true
    headers:
      Authorization: "Bearer ${CRM_MCP_TOKEN}"
```

将密钥存储在 `.env` 中，通过部署环境的密钥注入机制引用。

## 模板：stdio MCP（本地脚本）

```yaml
mcp_servers:
  - name: company-tools
    transport: stdio
    command: python
    args: ["/opt/mcp/company_tools_server.py"]
    enabled: true
```

将脚本挂载到 worker 容器，或通过 HTTP 在 sidecar 中运行 MCP。

## 验证工具

1. 新建会话
2. 提问：*What MCP tools do you have available?*
3. 调用工具：*Use the Jina reader to summarize https://example.com/docs*

## 安全清单

- [ ] MCP 服务器与 OpenCitadel 运行在同一信任域
- [ ] 使用最小权限的服务账号
- [ ] 通过 `audit_service` 日志审计工具调用
- [ ] 禁用未使用的 MCP 服务器（`enabled: false`）

## 路线图

Phase 2 将在 Settings 中添加 **一键 MCP 目录 UI**。

## 下一步

- [系统架构](../architecture/overview.zh-CN.md)
- [贡献指南](../../.github/CONTRIBUTING.zh-CN.md)
