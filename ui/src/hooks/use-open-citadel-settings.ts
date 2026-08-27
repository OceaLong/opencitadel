"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  type A2AServer,
  type CreateMCPServerRequest,
  integrationsApi,
  type MCPServer,
  type UpdateMCPServerRequest,
} from "@/lib/api";

export type SettingTab =
  | "common-setting"
  | "inference-setting"
  | "skills-setting"
  | "memory-setting"
  | "integrations-setting"
  | "runtime-setting";

export function useOpenCitadelSettings(open: boolean) {
  const t = useTranslations("settings");
  const tErrors = useTranslations("errors");
  const tCommon = useTranslations("common");
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [a2aServers, setA2aServers] = useState<A2AServer[]>([]);
  const [loadingMCP, setLoadingMCP] = useState(false);
  const [loadingA2A, setLoadingA2A] = useState(false);
  const fetchingRef = useRef(false);

  const refreshMcpServersSilently = useCallback(async () => {
    try {
      setMcpServers((await integrationsApi.listMCPServers()).items);
    } catch {
      // A mutation has already completed; the next panel load will reconcile state.
    }
  }, []);

  const refreshA2aServersSilently = useCallback(async () => {
    try {
      setA2aServers((await integrationsApi.listA2AServers()).items);
    } catch {
      // A mutation has already completed; the next panel load will reconcile state.
    }
  }, []);

  const fetchAllIntegrations = useCallback(() => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;

    setLoadingMCP(true);
    integrationsApi
      .listMCPServers()
      .then((data) => setMcpServers(data.items))
      .catch(() => toast.error(t("toastLoadMcpFailed")))
      .finally(() => setLoadingMCP(false));

    setLoadingA2A(true);
    integrationsApi
      .listA2AServers()
      .then((data) => setA2aServers(data.items))
      .catch(() => toast.error(t("toastLoadA2aFailed")))
      .finally(() => setLoadingA2A(false));
  }, [t]);

  useEffect(() => {
    if (open) {
      const timer = window.setTimeout(fetchAllIntegrations, 0);
      return () => window.clearTimeout(timer);
    }
    fetchingRef.current = false;
  }, [open, fetchAllIntegrations]);

  const handleMCPToggle = useCallback(
    async (serverId: string, enabled: boolean) => {
      const server = mcpServers.find((item) => item.id === serverId);
      setMcpServers((previous) =>
        previous.map((item) => (item.id === serverId ? { ...item, enabled } : item)),
      );
      try {
        const updated = await integrationsApi.setMCPServerEnabled(serverId, { enabled });
        setMcpServers((previous) =>
          previous.map((item) => (item.id === serverId ? updated : item)),
        );
        toast.success(
          t("toastServerToggled", {
            name: server?.name ?? serverId,
            state: enabled ? tCommon("enabled") : tCommon("disabledState"),
          }),
        );
      } catch {
        setMcpServers((previous) =>
          previous.map((item) => (item.id === serverId ? { ...item, enabled: !enabled } : item)),
        );
        toast.error(tErrors("operationFailedRetry"));
      }
    },
    [mcpServers, t, tCommon, tErrors],
  );

  const handleMCPDelete = useCallback(
    async (serverId: string) => {
      const previous = mcpServers;
      const target = previous.find((server) => server.id === serverId);
      setMcpServers((servers) => servers.filter((server) => server.id !== serverId));
      try {
        await integrationsApi.deleteMCPServer(serverId);
        toast.success(t("toastMcpDeleted", { name: target?.name ?? serverId }));
      } catch {
        setMcpServers(previous);
        toast.error(tErrors("deleteFailedRetry"));
      }
    },
    [mcpServers, t, tErrors],
  );

  const handleMCPEdit = useCallback(
    async (serverId: string, body: UpdateMCPServerRequest): Promise<boolean> => {
      try {
        const updated = await integrationsApi.updateMCPServer(serverId, body);
        setMcpServers((previous) =>
          previous.map((server) => (server.id === serverId ? updated : server)),
        );
        toast.success(t("toastMcpUpdated"));
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : tErrors("updateFailed"));
        return false;
      }
    },
    [t, tErrors],
  );

  const handleMCPAdd = useCallback(
    async (configText: string): Promise<boolean> => {
      try {
        const parsed = JSON.parse(configText) as CreateMCPServerRequest;
        const created = await integrationsApi.createMCPServer(parsed);
        setMcpServers((previous) => [...previous, created]);
        toast.success(t("toastMcpAdded"));
        await refreshMcpServersSilently();
        return true;
      } catch (error) {
        toast.error(
          error instanceof SyntaxError
            ? tErrors("jsonInvalid")
            : error instanceof Error
              ? error.message
              : tErrors("addFailed"),
        );
        return false;
      }
    },
    [t, tErrors, refreshMcpServersSilently],
  );

  const handleA2AToggle = useCallback(
    async (serverId: string, enabled: boolean) => {
      const server = a2aServers.find((item) => item.id === serverId);
      setA2aServers((previous) =>
        previous.map((item) => (item.id === serverId ? { ...item, enabled } : item)),
      );
      try {
        const updated = await integrationsApi.setA2AServerEnabled(serverId, { enabled });
        setA2aServers((previous) =>
          previous.map((item) => (item.id === serverId ? updated : item)),
        );
        toast.success(
          t("toastServerToggled", {
            name: server?.base_url ?? serverId,
            state: enabled ? tCommon("enabled") : tCommon("disabledState"),
          }),
        );
      } catch {
        setA2aServers((previous) =>
          previous.map((item) => (item.id === serverId ? { ...item, enabled: !enabled } : item)),
        );
        toast.error(tErrors("operationFailedRetry"));
      }
    },
    [a2aServers, t, tCommon, tErrors],
  );

  const handleA2ADelete = useCallback(
    async (serverId: string) => {
      const previous = a2aServers;
      const target = previous.find((server) => server.id === serverId);
      setA2aServers((servers) => servers.filter((server) => server.id !== serverId));
      try {
        await integrationsApi.deleteA2AServer(serverId);
        toast.success(t("toastA2aDeleted", { name: target?.base_url ?? serverId }));
      } catch {
        setA2aServers(previous);
        toast.error(tErrors("deleteFailedRetry"));
      }
    },
    [a2aServers, t, tErrors],
  );

  const handleA2AAdd = useCallback(
    async (baseUrl: string): Promise<boolean> => {
      try {
        const created = await integrationsApi.createA2AServer({
          base_url: baseUrl,
          enabled: true,
          visibility: "private",
        });
        setA2aServers((previous) => [...previous, created]);
        toast.success(t("toastA2aAdded"));
        await refreshA2aServersSilently();
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : tErrors("addFailed"));
        return false;
      }
    },
    [t, tErrors, refreshA2aServersSilently],
  );

  return {
    mcpServers,
    a2aServers,
    loadingMCP,
    loadingA2A,
    handleMCPToggle,
    handleMCPDelete,
    handleMCPAdd,
    handleMCPEdit,
    handleA2AToggle,
    handleA2ADelete,
    handleA2AAdd,
  };
}
