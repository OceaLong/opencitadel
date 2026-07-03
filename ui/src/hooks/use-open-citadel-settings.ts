"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { AgentConfig, ListA2AServerItem, ListMCPServerItem } from "@/lib/api";
import { configApi } from "@/lib/api";

export type SettingTab =
  | "common-setting"
  | "models-setting"
  | "skills-setting"
  | "memory-setting"
  | "integrations-setting";

export function useOpenCitadelSettings(open: boolean, activeSetting: SettingTab) {
  const [agentConfig, setAgentConfig] = useState<AgentConfig>({});
  const [mcpServers, setMcpServers] = useState<ListMCPServerItem[]>([]);
  const [a2aServers, setA2aServers] = useState<ListA2AServerItem[]>([]);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [loadingMCP, setLoadingMCP] = useState(false);
  const [loadingA2A, setLoadingA2A] = useState(false);
  const [saving, setSaving] = useState(false);
  const fetchingRef = useRef(false);

  const fetchAllConfigs = useCallback(() => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;

    setLoadingConfig(true);
    configApi
      .getAgentConfig()
      .then(setAgentConfig)
      .catch(() => {
        toast.error("获取基础配置失败");
      })
      .finally(() => {
        setLoadingConfig(false);
      });

    setLoadingMCP(true);
    configApi
      .getMCPServers()
      .then((data) => {
        setMcpServers(data?.mcp_servers ?? []);
      })
      .catch(() => {
        toast.error("获取 MCP 服务器列表失败");
      })
      .finally(() => {
        setLoadingMCP(false);
      });

    setLoadingA2A(true);
    configApi
      .getA2AServers()
      .then((data) => {
        setA2aServers(data?.a2a_servers ?? []);
      })
      .catch(() => {
        toast.error("获取 A2A 服务器列表失败");
      })
      .finally(() => {
        setLoadingA2A(false);
      });
  }, []);

  useEffect(() => {
    if (open) {
      fetchAllConfigs();
      return;
    }
    fetchingRef.current = false;
  }, [open, fetchAllConfigs]);

  const handleSave = async () => {
    setSaving(true);
    try {
      if (activeSetting === "common-setting") {
        await configApi.updateAgentConfig(agentConfig);
        toast.success("通用配置保存成功");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "保存失败";
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleMCPToggle = useCallback(async (serverName: string, enabled: boolean) => {
    setMcpServers((prev) =>
      prev.map((server) => (server.server_name === serverName ? { ...server, enabled } : server)),
    );
    try {
      await configApi.updateMCPServerEnabled(serverName, enabled);
      toast.success(`${serverName} 已${enabled ? "启用" : "禁用"}`);
    } catch {
      setMcpServers((prev) =>
        prev.map((server) =>
          server.server_name === serverName ? { ...server, enabled: !enabled } : server,
        ),
      );
      toast.error("操作失败，请重试");
    }
  }, []);

  const handleMCPDelete = useCallback(
    async (serverName: string) => {
      const prev = mcpServers;
      setMcpServers((list) => list.filter((server) => server.server_name !== serverName));
      try {
        await configApi.deleteMCPServer(serverName);
        toast.success(`已删除 MCP 服务器「${serverName}」`);
      } catch {
        setMcpServers(prev);
        toast.error("删除失败，请重试");
      }
    },
    [mcpServers],
  );

  const handleMCPAdd = useCallback(async (configText: string): Promise<boolean> => {
    try {
      const parsed = JSON.parse(configText);
      await configApi.addMCPServer(parsed);
      toast.success("MCP 服务器添加成功");

      try {
        const data = await configApi.getMCPServers();
        setMcpServers(data?.mcp_servers ?? []);
      } catch {
        // Best-effort refresh; the add result has already been saved.
      }
      return true;
    } catch (err) {
      if (err instanceof SyntaxError) {
        toast.error("JSON 格式错误，请检查配置");
      } else {
        toast.error(err instanceof Error ? err.message : "添加失败");
      }
      return false;
    }
  }, []);

  const handleA2AToggle = useCallback(
    async (id: string, enabled: boolean) => {
      setA2aServers((prev) =>
        prev.map((server) => (server.id === id ? { ...server, enabled } : server)),
      );
      try {
        await configApi.updateA2AServerEnabled(id, enabled);
        const server = a2aServers.find((item) => item.id === id);
        toast.success(`${server?.name ?? "Agent"} 已${enabled ? "启用" : "禁用"}`);
      } catch {
        setA2aServers((prev) =>
          prev.map((server) => (server.id === id ? { ...server, enabled: !enabled } : server)),
        );
        toast.error("操作失败，请重试");
      }
    },
    [a2aServers],
  );

  const handleA2ADelete = useCallback(
    async (id: string) => {
      const prev = a2aServers;
      const target = a2aServers.find((server) => server.id === id);
      setA2aServers((list) => list.filter((server) => server.id !== id));
      try {
        await configApi.deleteA2AServer(id);
        toast.success(`已删除 A2A Agent「${target?.name ?? id}」`);
      } catch {
        setA2aServers(prev);
        toast.error("删除失败，请重试");
      }
    },
    [a2aServers],
  );

  const handleA2AAdd = useCallback(async (baseUrl: string): Promise<boolean> => {
    try {
      await configApi.addA2AServer({ base_url: baseUrl });
      toast.success("远程 Agent 添加成功");

      try {
        const data = await configApi.getA2AServers();
        setA2aServers(data?.a2a_servers ?? []);
      } catch {
        // Best-effort refresh; the add result has already been saved.
      }
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "添加失败");
      return false;
    }
  }, []);

  return {
    agentConfig,
    setAgentConfig,
    mcpServers,
    a2aServers,
    loadingConfig,
    loadingMCP,
    loadingA2A,
    saving,
    handleSave,
    handleMCPToggle,
    handleMCPDelete,
    handleMCPAdd,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
  };
}
