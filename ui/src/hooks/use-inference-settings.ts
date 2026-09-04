"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  inferenceApi,
  type InferenceBinding,
  type InferenceBindingScope,
  type InferenceCapabilities,
  type InferenceEndpoint,
  type InferenceEndpointInput,
  type InferenceModel,
  type InferenceModelInput,
  type InferencePurpose,
} from "@/lib/api/inference";
import { loadInferenceSnapshot } from "@/lib/api/inference-cache";
import { clientDataScopeKey } from "@/lib/data/client-data-scope";
import { CAPABILITIES_CHANGED_EVENT, dispatchAppEvent } from "@/lib/events";
import { useClientDataScope } from "@/providers/client-data-provider";

export const inferenceProviders = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "ollama", label: "Ollama" },
  { value: "azure", label: "Azure OpenAI" },
] as const;

export const defaultInferenceCapabilities: InferenceCapabilities = {
  vision: false,
  vision_with_tools: true,
  audio: false,
  video: false,
  image_generation: false,
  max_image_bytes: 5 * 1024 * 1024,
  max_images_per_request: 8,
  max_video_frames: 8,
  image_encoding: "data_url",
  structured_output: "auto",
};

export const emptyEndpointInput: InferenceEndpointInput = {
  display_name: "",
  provider: "openai",
  base_url: "https://api.openai.com/v1",
  credential: "",
  visibility: "private",
};

export function emptyModelInput(endpointId = ""): InferenceModelInput {
  return {
    endpoint_id: endpointId,
    display_name: "",
    model_name: "",
    kind: "chat",
    settings: { kind: "chat", temperature: 0.7, max_output_tokens: 8192 },
    input_price_per_million: 0,
    output_price_per_million: 0,
    extra_params: {},
    capabilities: defaultInferenceCapabilities,
    visibility: "private",
  };
}

export function providerSupportsKind(
  provider: InferenceEndpoint["provider"],
  kind: InferenceModelInput["kind"],
): boolean {
  return kind === "chat" || ["openai", "azure", "ollama"].includes(provider);
}

export function useInferenceSettings() {
  const t = useTranslations("settingsInference");
  const tCommon = useTranslations("common");
  const { scope, loadResource, invalidateResource } = useClientDataScope();
  const scopeKey = scope ? clientDataScopeKey(scope) : null;
  const requestSequence = useRef(0);
  const [loaded, setLoaded] = useState<{
    scopeKey: string;
    endpoints: InferenceEndpoint[];
    models: InferenceModel[];
    bindings: InferenceBinding[];
  } | null>(null);
  const endpoints = loaded?.scopeKey === scopeKey ? loaded.endpoints : [];
  const models = loaded?.scopeKey === scopeKey ? loaded.models : [];
  const bindings = loaded?.scopeKey === scopeKey ? loaded.bindings : [];
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probingId, setProbingId] = useState<string | null>(null);
  const [endpointDialogOpen, setEndpointDialogOpen] = useState(false);
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [editingEndpoint, setEditingEndpoint] = useState<InferenceEndpoint | null>(null);
  const [editingModel, setEditingModel] = useState<InferenceModel | null>(null);
  const [endpointInput, setEndpointInput] = useState<InferenceEndpointInput>(emptyEndpointInput);
  const [modelInput, setModelInput] = useState<InferenceModelInput>(emptyModelInput());

  const load = useCallback(async () => {
    const requestId = ++requestSequence.current;
    if (!scopeKey) {
      setLoaded(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const snapshot = await loadResource("inference", loadInferenceSnapshot);
      if (requestSequence.current === requestId) {
        setLoaded({ scopeKey, ...snapshot });
      }
    } catch (error) {
      if (requestSequence.current === requestId) {
        toast.error(error instanceof Error ? error.message : tCommon("loadFailed"));
      }
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, [loadResource, scopeKey, tCommon]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    invalidateResource("inference");
    await load();
  }, [invalidateResource, load]);

  /** 写操作成功后的统一刷新：广播能力变化（顶栏模型状态芯片立即重拉）再重载列表。 */
  const refreshAfterMutation = useCallback(async () => {
    dispatchAppEvent(CAPABILITIES_CHANGED_EVENT);
    await refresh();
  }, [refresh]);

  const openEndpointCreate = () => {
    setEditingEndpoint(null);
    setEndpointInput(emptyEndpointInput);
    setEndpointDialogOpen(true);
  };

  const openEndpointEdit = (endpoint: InferenceEndpoint) => {
    setEditingEndpoint(endpoint);
    setEndpointInput({
      display_name: endpoint.display_name,
      provider: endpoint.provider,
      base_url: endpoint.base_url,
      credential: "",
      visibility: endpoint.visibility,
    });
    setEndpointDialogOpen(true);
  };

  const saveEndpoint = async () => {
    if (!endpointInput.display_name.trim() || !endpointInput.base_url.trim()) {
      toast.error(t("fillEndpointRequiredFields"));
      return;
    }
    setSaving(true);
    try {
      if (editingEndpoint) {
        await inferenceApi.updateEndpoint(editingEndpoint.id, endpointInput);
      } else {
        await inferenceApi.createEndpoint(endpointInput);
      }
      setEndpointDialogOpen(false);
      await refreshAfterMutation();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    } finally {
      setSaving(false);
    }
  };

  const deleteEndpoint = async (id: string) => {
    try {
      await inferenceApi.deleteEndpoint(id);
      await refreshAfterMutation();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    }
  };

  const openModelCreate = (endpointId = endpoints[0]?.id ?? "") => {
    setEditingModel(null);
    setModelInput(emptyModelInput(endpointId));
    setModelDialogOpen(true);
  };

  const openModelEdit = (model: InferenceModel) => {
    setEditingModel(model);
    setModelInput({
      endpoint_id: model.endpoint_id,
      display_name: model.display_name,
      model_name: model.model_name,
      kind: model.kind,
      settings: model.settings,
      input_price_per_million: model.input_price_per_million,
      output_price_per_million: model.output_price_per_million,
      extra_params: model.extra_params,
      capabilities: model.capabilities,
      visibility: model.visibility,
    });
    setModelDialogOpen(true);
  };

  const saveModel = async () => {
    const endpoint = endpoints.find((item) => item.id === modelInput.endpoint_id);
    if (
      !endpoint ||
      !modelInput.display_name.trim() ||
      !modelInput.model_name.trim() ||
      !providerSupportsKind(endpoint.provider, modelInput.kind)
    ) {
      toast.error(t("fillModelRequiredFields"));
      return;
    }
    setSaving(true);
    try {
      if (editingModel) {
        await inferenceApi.updateModel(editingModel.id, modelInput);
      } else {
        await inferenceApi.createModel(modelInput);
      }
      setModelDialogOpen(false);
      await refreshAfterMutation();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    } finally {
      setSaving(false);
    }
  };

  const deleteModel = async (id: string) => {
    try {
      await inferenceApi.deleteModel(id);
      await refreshAfterMutation();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    }
  };

  const probeModel = async (id: string) => {
    setProbingId(id);
    try {
      const result = await inferenceApi.probeModel(id);
      if (result.status === "ok") toast.success(result.message);
      else toast.error(result.message);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    } finally {
      setProbingId(null);
    }
  };

  const setBinding = async (
    purpose: InferencePurpose,
    modelId: string,
    bindingScope: InferenceBindingScope,
  ) => {
    try {
      await inferenceApi.setBinding(purpose, {
        model_id: modelId,
        binding_scope: bindingScope,
      });
      await refreshAfterMutation();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    }
  };

  const deleteBinding = async (purpose: InferencePurpose, bindingScope: InferenceBindingScope) => {
    try {
      await inferenceApi.deleteBinding(purpose, bindingScope);
      await refreshAfterMutation();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("operationFailed"));
    }
  };

  return {
    endpoints,
    models,
    bindings,
    loading,
    saving,
    probingId,
    endpointDialogOpen,
    setEndpointDialogOpen,
    modelDialogOpen,
    setModelDialogOpen,
    editingEndpoint,
    editingModel,
    endpointInput,
    setEndpointInput,
    modelInput,
    setModelInput,
    openEndpointCreate,
    openEndpointEdit,
    saveEndpoint,
    deleteEndpoint,
    openModelCreate,
    openModelEdit,
    saveModel,
    deleteModel,
    probeModel,
    setBinding,
    deleteBinding,
    reload: refresh,
  };
}
