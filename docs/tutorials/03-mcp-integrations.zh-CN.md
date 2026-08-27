[English](03-mcp-integrations.md)

# 教程 3：通过 MCP 连接内部系统

使用 **Model Context Protocol (MCP)** 为 Agent 接入内部 API、数据库与 SaaS 工具，无需在平台核心代码中编写定制集成。

## OpenCitadel 中的 MCP

MCP 服务器暴露工具（如 `maps_geocode`、`read_url`），Agent 可像调用原生工具一样调用它们。OpenCitadel 支持：

- `stdio` — 本地进程
- `sse` / `streamable_http` — 远程 HTTP 服务

MCP Server 是一等、Owner Scope 的 Integration Resource。通过 **设置 → 集成** 或 `/api/integrations/mcp-servers` 创建和管理；Skill 与 Automation 使用稳定 Resource ID 引用。

## 示例：添加远程 MCP 服务器

打开 **设置 → 集成 → 添加服务器**，提交：

```json
{
  "name": "jina-mcp-server",
  "transport": "streamable_http",
  "url": "https://mcp.jina.ai/sse",
  "enabled": true,
  "visibility": "private"
}
```

无需重启服务。Integration List 会投影连接状态与发现的工具；Agent 工具使用 `mcp_` 前缀。

## 示例：内部 HTTP MCP 网关

对于内网系统，在 VPC 内运行 MCP 网关：

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

Credential 使用当前 API 加密密钥加密存储，读取时脱敏。不要把 Integration Credential 放入部署变量或 Runtime Policy。

## 模板：stdio MCP（本地脚本）

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

只有管理员可以创建 stdio 或 Global Resource。可将脚本挂载进执行内核容器；多副本场景优先使用所有内核均可达的 HTTP Sidecar。

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

打开 **设置 → 集成** 管理 MCP 与 A2A Resource。修改会立即持久化到 PostgreSQL；连接健康与能力发现由 Read-side Projection 提供。

## 下一步

- [系统架构](../architecture/overview.zh-CN.md)
- [贡献指南](../../.github/CONTRIBUTING.zh-CN.md)
