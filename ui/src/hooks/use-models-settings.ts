"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { modelsApi } from "@/lib/api/models";
import { invalidateModelsCache } from "@/lib/api/models-cache";
import type { CreateLLMModelParams, LLMModel, ModelCapabilities } from "@/lib/api/types";

export { SUPPORTED_PROVIDERS } from "@/hooks/use-endpoints-settings";

export const defaultCapabilities: ModelCapabilities = {
  vision: false,
  vision_with_tools: true,
  max_image_bytes: 5 * 1024 * 1024,
  max_images_per_request: 8,
  image_encoding: "data_url",
};

const emptyForm = (endpointId: string): CreateLLMModelParams => ({
  endpoint_id: endpointId,
  display_name: "",
  model_name: "",
  temperature: 0.7,
  max_tokens: 65536,
  capabilities: defaultCapabilities,
  supports_multimodal: false,
});

export function useModelsSettings(onModelsChanged?: () => void) {
  const t = useTranslations("settingsModels");
  const tCommon = useTranslations("common");
  const tErrors = useTranslations("errors");
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LLMModel | null>(null);
  const [form, setForm] = useState<CreateLLMModelParams>(emptyForm(""));
  const [displayNameAutoSync, setDisplayNameAutoSync] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probingId, setProbingId] = useState<string | null>(null);
  const [probeStatus, setProbeStatus] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await modelsApi.list();
      setModels(data.models);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [tCommon]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = (endpointId: string) => {
    setEditing(null);
    setForm(emptyForm(endpointId));
    setDisplayNameAutoSync(true);
    setDialogOpen(true);
  };

  const openEdit = (model: LLMModel) => {
    setEditing(model);
    setDisplayNameAutoSync(false);
    setForm({
      endpoint_id: model.endpoint_id,
      display_name: model.display_name,
      model_name: model.model_name,
      temperature: model.temperature,
      max_tokens: model.max_tokens,
      capabilities: model.capabilities ?? {
        ...defaultCapabilities,
        vision: model.supports_multimodal ?? false,
      },
      supports_multimodal: model.supports_multimodal ?? false,
      is_default: model.is_default,
    });
    setDialogOpen(true);
  };

  const updateModelName = useCallback(
    (value: string) => {
      setForm((prev) =>
        displayNameAutoSync
          ? { ...prev, model_name: value, display_name: value }
          : { ...prev, model_name: value },
      );
    },
    [displayNameAutoSync],
  );

  const updateDisplayName = useCallback((value: string) => {
    setDisplayNameAutoSync(false);
    setForm((prev) => ({ ...prev, display_name: value }));
  }, []);

  const handleSave = async () => {
    if (!form.display_name.trim() || !form.model_name.trim() || !form.endpoint_id.trim()) {
      toast.error(t("fillModelRequiredFields"));
      return;
    }

    setSaving(true);
    const payload = {
      ...form,
      supports_multimodal: form.capabilities?.vision ?? form.supports_multimodal ?? false,
      capabilities: {
        ...defaultCapabilities,
        ...form.capabilities,
        vision: form.capabilities?.vision ?? form.supports_multimodal ?? false,
      },
    };

    try {
      if (editing) {
        await modelsApi.update(editing.id, payload);
        toast.success(t("modelUpdated"));
      } else {
        await modelsApi.create(payload);
        toast.success(t("modelCreated"));
      }
      setDialogOpen(false);
      invalidateModelsCache();
      await load();
      onModelsChanged?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tErrors("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await modelsApi.delete(id);
      toast.success(t("deleted"));
      invalidateModelsCache();
      await load();
      onModelsChanged?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tErrors("deleteFailed"));
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await modelsApi.setDefault(id);
      toast.success(t("setDefaultSuccess"));
      invalidateModelsCache();
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("operationFailed"));
    }
  };

  const handleProbe = async (id: string) => {
    setProbingId(id);
    try {
      const result = await modelsApi.probeMultimodal(id);
      setProbeStatus((prev) => ({ ...prev, [id]: result.status }));
      if (result.status === "ok") {
        toast.success(result.message || t("multimodalProbeSuccess"));
      } else {
        toast.info(result.message || t("multimodalProbeDone"));
      }
    } catch (e) {
      setProbeStatus((prev) => ({ ...prev, [id]: "error" }));
      toast.error(e instanceof Error ? e.message : tErrors("probeFailed"));
    } finally {
      setProbingId(null);
    }
  };

  return {
    models,
    loading,
    dialogOpen,
    setDialogOpen,
    editing,
    form,
    setForm,
    saving,
    probingId,
    probeStatus,
    load,
    openCreate,
    openEdit,
    updateModelName,
    updateDisplayName,
    handleSave,
    handleDelete,
    handleSetDefault,
    handleProbe,
  };
}
