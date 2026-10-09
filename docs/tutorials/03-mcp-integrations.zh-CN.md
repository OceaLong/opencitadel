[English](03-mcp-integrations.md)

# 教程 3：通过 MCP 连接内部系统

使用 **Model Context Protocol (MCP)** 为 Agent 接入内部 API、数据库与 SaaS 工具，无需在平台核心代码中编写定制集成。

## OpenCitadel 中的 MCP

MCP 服务器暴露工具（如 `maps_geocode`、`read_url`），Agent 可像调用原生工具一样调用它们。OpenCitadel 支持：

- `stdio` — 本地进程
- `sse` / `streamable_http` — 远程 HTTP 服务

MCP Server 是一等、Owner Scope 的 Integration Resource。通过 **设置 → 集成** 或 `/api/integrations/mcp-servers` 创建和管理；Skill 与 Automation 使用稳定 Resource ID 引用。

## 示例：添加远程 MCP 服务器

将示例 URL 替换为已评审服务器的真实 Endpoint。在 **设置 → 集成 → 添加服务器** 中提交：

```json
{
  "name": "docs-reader",
  "transport": "streamable_http",
  "url": "https://mcp.example.com/mcp",
  "enabled": true,
  "visibility": "private"
}
```

无需重启服务。Integration List 展示持久化配置；执行内核在构建获授权 Agent Catalog 时
连接并发现工具，Agent 工具使用 `mcp_` 前缀。注册成功不代表连接探测成功。

## 示例：内部 HTTP MCP 网关

对于内网系统，在 VPC 内运行 MCP 网关：

```json
{
  "name": "internal-crm",
  "transport": "streamable_http",
  "url": "http://mcp-gateway.internal:8080/mcp",
  "enabled": true,
  "visibility": "private",
  "headers": { "Authorization": "Bearer <token>" }
}
```

部署必须通过 `OUTBOUND_PRIVATE_HOST_ALLOWLIST` 显式允许该内部 Host；注册仍须通过
出站 URL 校验。Transport 应匹配服务端协议：`/sse` Endpoint 可能需要 `sse`，
本示例假定使用 `streamable_http`。

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
2. 询问：_你有哪些 MCP 工具可用？_
3. 对获准来源调用一个已发现工具。
4. 复核出现的持久审批卡。没有管理员声明 Policy 的工具采用保守的 Interactive/Always
   Approval Policy；注册资源本身不授予只读执行权。只有管理员可通过管理 API 声明
   `tool_policies`。

## 安全清单

- [ ] MCP 服务器与 OpenCitadel 处于同一信任域
- [ ] 使用最小权限的服务账号
- [ ] 通过 `audit_service` 日志审计工具调用
- [ ] 禁用未使用的 MCP 服务器（`enabled: false`）

## 通过 UI 管理

打开 **设置 → 集成** 管理 MCP 与 A2A Resource。修改会立即持久化到 PostgreSQL；连接错误和已发现工具来自 Runtime Catalog，
应通过真正获授权的 Agent 任务验证。

## 下一步

- [系统架构](../architecture/overview.zh-CN.md)
- [贡献指南](../../.github/CONTRIBUTING.zh-CN.md)
