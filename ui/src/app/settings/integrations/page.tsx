"use client";

import { A2ASetting, MCPSetting } from "@/components/open-citadel-settings";
import { useOpenCitadelSettings } from "@/hooks/use-open-citadel-settings";

export default function IntegrationsSettingsPage() {
  const {
    mcpServers,
    a2aServers,
    loadingMCP,
    loadingA2A,
    handleMCPToggle,
    handleMCPDelete,
    handleMCPAdd,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
  } = useOpenCitadelSettings(true, "mcp-setting");

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">协议集成</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          配置 MCP 服务器与 A2A 远程 Agent，供 Skill 与会话任务调用。
        </p>
      </div>
      <MCPSetting
        servers={mcpServers}
        loading={loadingMCP}
        onToggleEnabled={handleMCPToggle}
        onDelete={handleMCPDelete}
        onAdd={handleMCPAdd}
      />
      <A2ASetting
        servers={a2aServers}
        loading={loadingA2A}
        onToggleEnabled={handleA2AToggle}
        onDelete={handleA2ADelete}
        onAdd={handleA2AAdd}
      />
    </div>
  );
}
