"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { modelsApi } from "@/lib/api/models";
import { invalidateModelsCache } from "@/lib/api/models-cache";
import type {
  CreateLLMModelParams,
  LLMModel,
  LLMProvider,
  ModelCapabilities,
} from "@/lib/api/types";

export const SUPPORTED_PROVIDERS: { value: LLMProvider; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "ollama", label: "Ollama" },
  { value: "azure", label: "Azure OpenAI" },
];

export const defaultCapabilities: ModelCapabilities = {
  vision: false,
  vision_with_tools: true,
  max_image_bytes: 5 * 1024 * 1024,
  max_images_per_request: 8,
  image_encoding: "data_url",
};

const emptyForm: CreateLLMModelParams = {
  display_name: "",
  provider: "openai",
  base_url: "https://api.openai.com/v1",
  api_key: "",
  model_name: "gpt-4o",
  temperature: 0.7,
  max_tokens: 8192,
  capabilities: defaultCapabilities,
  supports_multimodal: false,
};

export function useModelsSettings() {
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LLMModel | null>(null);
  const [form, setForm] = useState<CreateLLMModelParams>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [probingId, setProbingId] = useState<string | null>(null);
  const [probeStatus, setProbeStatus] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await modelsApi.list();
      setModels(data.models);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm);
    setDialogOpen(true);
  };

  const openEdit = (model: LLMModel) => {
    setEditing(model);
    setForm({
      display_name: model.display_name,
      provider: model.provider,
      base_url: model.base_url,
      api_key: "",
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

  const handleSave = async () => {
    if (!form.display_name.trim() || !form.model_name.trim() || !form.base_url.trim()) {
      toast.error("请填写显示名称、模型名和 Base URL");
      return;
    }
    if (!editing && !form.api_key?.trim()) {
      toast.error("新建模型时必须填写 API Key");
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
        toast.success("模型已更新");
      } else {
        await modelsApi.create(payload);
        toast.success("模型已创建");
      }
      setDialogOpen(false);
      invalidateModelsCache();
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await modelsApi.delete(id);
      toast.success("已删除");
      invalidateModelsCache();
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await modelsApi.setDefault(id);
      toast.success("已设为默认");
      invalidateModelsCache();
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const handleProbe = async (id: string) => {
    setProbingId(id);
    try {
      const result = await modelsApi.probeMultimodal(id);
      setProbeStatus((prev) => ({ ...prev, [id]: result.status }));
      if (result.status === "ok") {
        toast.success(result.message || "多模态探测成功");
      } else {
        toast.info(result.message || "多模态探测完成");
      }
    } catch (e) {
      setProbeStatus((prev) => ({ ...prev, [id]: "error" }));
      toast.error(e instanceof Error ? e.message : "探测失败");
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
    openCreate,
    openEdit,
    handleSave,
    handleDelete,
    handleSetDefault,
    handleProbe,
  };
}
