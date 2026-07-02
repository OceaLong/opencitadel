[English](03-mcp-integrations.md)

# 教程 3：通过 MCP 连接内部系统

使用 **Model Context Protocol (MCP)** 为 Agent 接入内部 API、数据库与 SaaS 工具，无需在平台核心代码中编写定制集成。

## OpenCitadel 中的 MCP

MCP 服务器暴露工具（如 `maps_geocode`、`read_url`），Agent 可像调用原生工具一样调用它们。OpenCitadel 支持：

- `stdio` — 本地进程
- `sse` / `streamable_http` — 远程 HTTP 服务

配置位于 `api/config.yaml` 的 `mcp_config.mcpServers`（以服务器名为键的字典）。也可在 **设置 → 集成**（`/settings/integrations`）中管理。

## 示例：添加远程 MCP 服务器

编辑 `api/config.yaml`：

```yaml
mcp_config:
  mcpServers:
    jina-mcp-server:
      transport: streamable_http
      url: https://mcp.jina.ai/sse
      enabled: true
```

重启 API 与 Worker：

```bash
docker compose restart opencitadel-api opencitadel-worker
```

工具会以 `mcp_` 前缀出现在 Agent 工具列表中。

## 示例：内部 HTTP MCP 网关

对于内网系统，在 VPC 内运行 MCP 网关：

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

密钥存放在 `.env` 中，通过部署环境的 Secret 注入机制引用。

## 模板：stdio MCP（本地脚本）

```yaml
mcp_config:
  mcpServers:
    company-tools:
      transport: stdio
      command: python
      args: ["/opt/mcp/company_tools_server.py"]
      enabled: true
```

将脚本挂载进 Worker 容器，或通过 HTTP 可达的 sidecar 运行 MCP。

## 验证工具

1. 创建会话
2. 询问：*你有哪些 MCP 工具可用？*
3. 调用工具：*用 Jina reader 总结 https://example.com/docs*

## 安全清单

- [ ] MCP 服务器与 OpenCitadel 处于同一信任域
- [ ] 使用最小权限的服务账号
- [ ] 通过 `audit_service` 日志审计工具调用
- [ ] 禁用未使用的 MCP 服务器（`enabled: false`）

## 通过 UI 管理

打开 **设置 → 集成** 可查看 MCP 与 A2A 配置；当 `USE_DB_APP_CONFIG=true` 时，运行时修改会持久化到数据库。

## 下一步

- [系统架构](../architecture/overview.zh-CN.md)
- [贡献指南](../../.github/CONTRIBUTING.zh-CN.md)
