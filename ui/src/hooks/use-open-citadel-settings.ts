"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { AgentConfig, ListA2AServerItem, ListMCPServerItem, MCPServerConfig } from "@/lib/api";
import { configApi } from "@/lib/api";

export type SettingTab =
  | "common-setting"
  | "models-setting"
  | "skills-setting"
  | "memory-setting"
  | "integrations-setting"
  | "runtime-setting";

export function useOpenCitadelSettings(open: boolean, activeSetting: SettingTab) {
  const t = useTranslations("settings");
  const tErrors = useTranslations("errors");
  const tCommon = useTranslations("common");
  const [agentConfig, setAgentConfig] = useState<AgentConfig>({});
  const [mcpServers, setMcpServers] = useState<ListMCPServerItem[]>([]);
  const [a2aServers, setA2aServers] = useState<ListA2AServerItem[]>([]);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [loadingMCP, setLoadingMCP] = useState(false);
  const [loadingA2A, setLoadingA2A] = useState(false);
  const [saving, setSaving] = useState(false);
  const fetchingRef = useRef(false);

  const refreshMcpServersSilently = useCallback(async () => {
    try {
      const data = await configApi.getMCPServers();
      setMcpServers(data?.mcp_servers ?? []);
    } catch {
      // Best-effort silent refresh after MCP mutations.
    }
  }, []);

  const fetchAllConfigs = useCallback(() => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;

    setLoadingConfig(true);
    configApi
      .getAgentConfig()
      .then(setAgentConfig)
      .catch(() => {
        toast.error(t("toastLoadAgentConfigFailed"));
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
        toast.error(t("toastLoadMcpFailed"));
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
        toast.error(t("toastLoadA2aFailed"));
      })
      .finally(() => {
        setLoadingA2A(false);
      });
  }, [t]);

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
        toast.success(t("toastAgentConfigSaved"));
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : tErrors("saveFailed");
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleMCPToggle = useCallback(
    async (serverName: string, enabled: boolean) => {
      setMcpServers((prev) =>
        prev.map((server) => (server.server_name === serverName ? { ...server, enabled } : server)),
      );
      try {
        await configApi.updateMCPServerEnabled(serverName, enabled);
        toast.success(
          t("toastServerToggled", {
            name: serverName,
            state: enabled ? tCommon("enabled") : tCommon("disabledState"),
          }),
        );
        await refreshMcpServersSilently();
      } catch {
        setMcpServers((prev) =>
          prev.map((server) =>
            server.server_name === serverName ? { ...server, enabled: !enabled } : server,
          ),
        );
        toast.error(tErrors("operationFailedRetry"));
      }
    },
    [t, tCommon, tErrors, refreshMcpServersSilently],
  );

  const handleMCPDelete = useCallback(
    async (serverName: string) => {
      const prev = mcpServers;
      setMcpServers((list) => list.filter((server) => server.server_name !== serverName));
      try {
        await configApi.deleteMCPServer(serverName);
        toast.success(t("toastMcpDeleted", { name: serverName }));
      } catch {
        setMcpServers(prev);
        toast.error(tErrors("deleteFailedRetry"));
      }
    },
    [mcpServers, t, tErrors],
  );

  const handleMCPEdit = useCallback(
    async (serverName: string, config: MCPServerConfig): Promise<boolean> => {
      try {
        await configApi.updateMCPServer(serverName, { mcpServers: { [serverName]: config } });
        toast.success(t("toastMcpUpdated"));
        await refreshMcpServersSilently();
        return true;
      } catch (err) {
        toast.error(err instanceof Error ? err.message : tErrors("updateFailed"));
        return false;
      }
    },
    [t, tErrors, refreshMcpServersSilently],
  );

  const handleMCPAdd = useCallback(
    async (configText: string): Promise<boolean> => {
      try {
        const parsed = JSON.parse(configText);
        await configApi.addMCPServer(parsed);
        toast.success(t("toastMcpAdded"));
        await refreshMcpServersSilently();
        return true;
      } catch (err) {
        if (err instanceof SyntaxError) {
          toast.error(tErrors("jsonInvalid"));
        } else {
          toast.error(err instanceof Error ? err.message : tErrors("addFailed"));
        }
        return false;
      }
    },
    [t, tErrors, refreshMcpServersSilently],
  );

  const handleA2AToggle = useCallback(
    async (id: string, enabled: boolean) => {
      setA2aServers((prev) =>
        prev.map((server) => (server.id === id ? { ...server, enabled } : server)),
      );
      try {
        await configApi.updateA2AServerEnabled(id, enabled);
        const server = a2aServers.find((item) => item.id === id);
        toast.success(
          t("toastServerToggled", {
            name: server?.name ?? "Agent",
            state: enabled ? tCommon("enabled") : tCommon("disabledState"),
          }),
        );
      } catch {
        setA2aServers((prev) =>
          prev.map((server) => (server.id === id ? { ...server, enabled: !enabled } : server)),
        );
        toast.error(tErrors("operationFailedRetry"));
      }
    },
    [a2aServers, t, tCommon, tErrors],
  );

  const handleA2ADelete = useCallback(
    async (id: string) => {
      const prev = a2aServers;
      const target = a2aServers.find((server) => server.id === id);
      setA2aServers((list) => list.filter((server) => server.id !== id));
      try {
        await configApi.deleteA2AServer(id);
        toast.success(t("toastA2aDeleted", { name: target?.name ?? id }));
      } catch {
        setA2aServers(prev);
        toast.error(tErrors("deleteFailedRetry"));
      }
    },
    [a2aServers, t, tErrors],
  );

  const handleA2AAdd = useCallback(
    async (baseUrl: string): Promise<boolean> => {
      try {
        await configApi.addA2AServer({ base_url: baseUrl });
        toast.success(t("toastA2aAdded"));

        try {
          const data = await configApi.getA2AServers();
          setA2aServers(data?.a2a_servers ?? []);
        } catch {
          // Best-effort refresh; the add result has already been saved.
        }
        return true;
      } catch (err) {
        toast.error(err instanceof Error ? err.message : tErrors("addFailed"));
        return false;
      }
    },
    [t, tErrors],
  );

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
    handleMCPEdit,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
  };
}
