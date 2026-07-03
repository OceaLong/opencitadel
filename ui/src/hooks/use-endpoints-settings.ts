"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { endpointsApi } from "@/lib/api/endpoints";
import type { CreateLLMEndpointParams, LLMEndpoint, LLMProvider } from "@/lib/api/types";

export const SUPPORTED_PROVIDERS: { value: LLMProvider; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "ollama", label: "Ollama" },
  { value: "azure", label: "Azure OpenAI" },
];

const emptyEndpointForm: CreateLLMEndpointParams = {
  display_name: "",
  provider: "openai",
  base_url: "https://api.openai.com/v1",
  api_key: "",
};

export function useEndpointsSettings() {
  const t = useTranslations("settingsModels");
  const tCommon = useTranslations("common");
  const tErrors = useTranslations("errors");
  const [endpoints, setEndpoints] = useState<LLMEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LLMEndpoint | null>(null);
  const [form, setForm] = useState<CreateLLMEndpointParams>(emptyEndpointForm);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await endpointsApi.list();
      setEndpoints(data.endpoints);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [tCommon]);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm(emptyEndpointForm);
    setDialogOpen(true);
  };

  const openEdit = (endpoint: LLMEndpoint) => {
    setEditing(endpoint);
    setForm({
      display_name: endpoint.display_name,
      provider: endpoint.provider,
      base_url: endpoint.base_url,
      api_key: "",
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.display_name.trim() || !form.base_url.trim()) {
      toast.error(t("fillEndpointRequiredFields"));
      return;
    }
    if (!editing && form.provider !== "ollama" && !form.api_key?.trim()) {
      toast.error(t("apiKeyRequired"));
      return;
    }

    setSaving(true);
    try {
      if (editing) {
        await endpointsApi.update(editing.id, form);
        toast.success(t("endpointUpdated"));
      } else {
        await endpointsApi.create(form);
        toast.success(t("endpointCreated"));
      }
      setDialogOpen(false);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tErrors("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await endpointsApi.delete(id);
      toast.success(t("deleted"));
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tErrors("deleteFailed"));
    }
  };

  return {
    endpoints,
    loading,
    dialogOpen,
    setDialogOpen,
    editing,
    form,
    setForm,
    saving,
    openCreate,
    openEdit,
    handleSave,
    handleDelete,
    reload: load,
  };
}
