"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

import {
  inferenceProviders,
  providerSupportsKind,
  useInferenceSettings,
} from "@/hooks/use-inference-settings";
import type {
  ChatModelSettings,
  InferenceModel,
  InferenceModelKind,
  InferenceProvider,
  InferencePurpose,
  ResourceVisibility,
} from "@/lib/api/inference";
import {
  bindingIsInherited,
  bindingSelectionValue,
  modelsForPurpose,
} from "@/lib/api/inference-cache";

type Props = {
  embedded?: boolean;
  isAdmin?: boolean;
  userId?: string;
};

const purposes: InferencePurpose[] = ["chat", "embedding", "rerank"];

function canManage(
  resource: {
    visibility: ResourceVisibility;
    owner_user_id?: string | null;
    team_id?: string | null;
  },
  isAdmin: boolean,
  userId?: string,
) {
  return (
    isAdmin ||
    (resource.visibility === "private" &&
      (Boolean(resource.team_id) || resource.owner_user_id === userId))
  );
}

export function InferenceSettings({ embedded = false, isAdmin = false, userId }: Props) {
  const state = useInferenceSettings();
  const t = useTranslations("settingsInference");
  const tCommon = useTranslations("common");
  const modelsByEndpoint = useMemo(() => {
    const grouped = new Map<string, InferenceModel[]>();
    for (const model of state.models) {
      grouped.set(model.endpoint_id, [...(grouped.get(model.endpoint_id) ?? []), model]);
    }
    return grouped;
  }, [state.models]);

  const updateKind = (kind: InferenceModelKind) => {
    state.setModelInput({
      ...state.modelInput,
      kind,
      settings:
        kind === "chat"
          ? { kind: "chat", temperature: 0.7, max_output_tokens: 8192 }
          : { kind: "embedding", dimensions: 1536, max_batch_size: 64 },
      capabilities:
        kind === "chat"
          ? state.modelInput.capabilities
          : {
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
            },
    });
  };

  const updateChatSettings = (patch: Partial<ChatModelSettings>) => {
    const settings = state.modelInput.settings;
    if (settings.kind !== "chat") return;
    state.setModelInput({
      ...state.modelInput,
      settings: { ...settings, ...patch, kind: "chat" },
    });
  };

  return (
    <div className={embedded ? "w-full px-1" : "max-w-5xl"}>
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h2 className={embedded ? "text-lg font-semibold" : "text-2xl font-semibold"}>
            {t("inferenceTitle")}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">{t("inferenceDescription")}</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size={embedded ? "xs" : "default"}
            onClick={state.openEndpointCreate}
          >
            <Plus className="mr-1 size-4" />
            {t("addEndpoint")}
          </Button>
          <Button
            size={embedded ? "xs" : "default"}
            disabled={state.endpoints.length === 0}
            onClick={() => state.openModelCreate()}
          >
            <Plus className="mr-1 size-4" />
            {t("addModel")}
          </Button>
        </div>
      </div>

      <Card className="mb-5">
        <CardHeader>
          <CardTitle>{t("bindingsTitle")}</CardTitle>
          <CardDescription>{t("bindingsDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          {purposes.map((purpose) => {
            const binding = state.bindings.find((item) => item.purpose === purpose);
            const candidates = modelsForPurpose(state.models, purpose);
            const inherited = bindingIsInherited(binding);
            return (
              <div key={purpose} className="border-border rounded-lg border p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <Label className="capitalize">{t(`purpose_${purpose}`)}</Label>
                  {inherited ? <Badge variant="outline">{t("inheritedGlobal")}</Badge> : null}
                </div>
                <Select
                  value={bindingSelectionValue(binding)}
                  onValueChange={(modelId) => {
                    if (modelId === "inherit") {
                      void state.deleteBinding(purpose, "workspace");
                    } else {
                      void state.setBinding(purpose, modelId, "workspace");
                    }
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="inherit">{t("inheritBinding")}</SelectItem>
                    {candidates.map((model) => (
                      <SelectItem key={model.id} value={model.id}>
                        {model.display_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {state.loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : state.endpoints.length === 0 ? (
        <EmptyState title={t("noEndpoints")} className="py-8" />
      ) : (
        <div className="grid gap-4">
          {state.endpoints.map((endpoint) => (
            <Card key={endpoint.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle className="flex flex-wrap items-center gap-2">
                      {endpoint.display_name}
                      <Badge variant="secondary">{endpoint.provider}</Badge>
                      <Badge variant="outline">{endpoint.visibility}</Badge>
                      <Badge variant={endpoint.credential_configured ? "secondary" : "destructive"}>
                        {endpoint.credential_configured
                          ? t("credentialConfigured")
                          : t("credentialMissing")}
                      </Badge>
                    </CardTitle>
                    <CardDescription className="mt-1 break-all">
                      {endpoint.base_url}
                    </CardDescription>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => state.openModelCreate(endpoint.id)}
                    >
                      {t("addModelUnderEndpoint")}
                    </Button>
                    {canManage(endpoint, isAdmin, userId) ? (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => state.openEndpointEdit(endpoint)}
                        >
                          {tCommon("edit")}
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => void state.deleteEndpoint(endpoint.id)}
                        >
                          <Trash2 className="text-destructive size-4" />
                        </Button>
                      </>
                    ) : null}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {(modelsByEndpoint.get(endpoint.id) ?? []).map((model) => (
                  <div
                    key={model.id}
                    className="border-border flex items-center justify-between gap-3 rounded-lg border p-3"
                  >
                    <div>
                      <div className="flex flex-wrap items-center gap-2 font-medium">
                        {model.display_name}
                        <Badge variant="outline">{model.kind}</Badge>
                        <span className="text-muted-foreground text-xs">{model.model_name}</span>
                      </div>
                      <p className="text-muted-foreground mt-1 text-xs">
                        {model.kind === "embedding"
                          ? `1536 · batch ${model.settings.kind === "embedding" ? model.settings.max_batch_size : "-"}`
                          : `temperature ${model.settings.kind === "chat" ? model.settings.temperature : "-"}`}
                      </p>
                    </div>
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={state.probingId === model.id}
                        onClick={() => void state.probeModel(model.id)}
                      >
                        {state.probingId === model.id ? t("probing") : t("probeModel")}
                      </Button>
                      {canManage(model, isAdmin, userId) ? (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => state.openModelEdit(model)}
                          >
                            {tCommon("edit")}
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => void state.deleteModel(model.id)}
                          >
                            <Trash2 className="text-destructive size-4" />
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                ))}
                {(modelsByEndpoint.get(endpoint.id) ?? []).length === 0 ? (
                  <p className="text-muted-foreground py-3 text-sm">{t("noModelsUnderEndpoint")}</p>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={state.endpointDialogOpen} onOpenChange={state.setEndpointDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {state.editingEndpoint ? t("editEndpoint") : t("addEndpoint")}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>{t("endpointDisplayName")}</Label>
              <Input
                value={state.endpointInput.display_name}
                onChange={(event) =>
                  state.setEndpointInput({
                    ...state.endpointInput,
                    display_name: event.target.value,
                  })
                }
              />
            </div>
            <div>
              <Label>{t("provider")}</Label>
              <Select
                value={state.endpointInput.provider}
                onValueChange={(value) =>
                  state.setEndpointInput({
                    ...state.endpointInput,
                    provider: value as InferenceProvider,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {inferenceProviders.map((provider) => (
                    <SelectItem key={provider.value} value={provider.value}>
                      {provider.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t("baseUrl")}</Label>
              <Input
                value={state.endpointInput.base_url}
                onChange={(event) =>
                  state.setEndpointInput({ ...state.endpointInput, base_url: event.target.value })
                }
              />
            </div>
            <div>
              <Label>{state.editingEndpoint ? t("credentialLeaveBlank") : t("credential")}</Label>
              <Input
                type="password"
                value={state.endpointInput.credential}
                onChange={(event) =>
                  state.setEndpointInput({ ...state.endpointInput, credential: event.target.value })
                }
              />
            </div>
            {isAdmin ? (
              <div>
                <Label>{t("visibility")}</Label>
                <Select
                  value={state.endpointInput.visibility}
                  onValueChange={(value) =>
                    state.setEndpointInput({
                      ...state.endpointInput,
                      visibility: value as ResourceVisibility,
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">{t("visibilityPrivate")}</SelectItem>
                    <SelectItem value="global">{t("visibilityGlobal")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => state.setEndpointDialogOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button disabled={state.saving} onClick={() => void state.saveEndpoint()}>
              {state.saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={state.modelDialogOpen} onOpenChange={state.setModelDialogOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{state.editingModel ? t("editModel") : t("addModel")}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label>{t("endpoint")}</Label>
              <Select
                value={state.modelInput.endpoint_id}
                onValueChange={(value) =>
                  state.setModelInput({ ...state.modelInput, endpoint_id: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {state.endpoints.map((endpoint) => (
                    <SelectItem key={endpoint.id} value={endpoint.id}>
                      {endpoint.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t("modelKind")}</Label>
              <Select
                value={state.modelInput.kind}
                disabled={Boolean(state.editingModel)}
                onValueChange={(value) => updateKind(value as InferenceModelKind)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["chat", "embedding"] as const)
                    .filter((kind) => {
                      const endpoint = state.endpoints.find(
                        (item) => item.id === state.modelInput.endpoint_id,
                      );
                      return !endpoint || providerSupportsKind(endpoint.provider, kind);
                    })
                    .map((kind) => (
                      <SelectItem key={kind} value={kind}>
                        {kind}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t("displayName")}</Label>
              <Input
                value={state.modelInput.display_name}
                onChange={(event) =>
                  state.setModelInput({ ...state.modelInput, display_name: event.target.value })
                }
              />
            </div>
            <div>
              <Label>{t("modelName")}</Label>
              <Input
                value={state.modelInput.model_name}
                onChange={(event) =>
                  state.setModelInput({ ...state.modelInput, model_name: event.target.value })
                }
              />
            </div>
            {state.modelInput.settings.kind === "chat" ? (
              <>
                <div>
                  <Label>{t("temperature")}</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={state.modelInput.settings.temperature}
                    onChange={(event) =>
                      updateChatSettings({ temperature: Number(event.target.value) })
                    }
                  />
                </div>
                <div>
                  <Label>{t("maxTokens")}</Label>
                  <Input
                    type="number"
                    value={state.modelInput.settings.max_output_tokens}
                    onChange={(event) =>
                      updateChatSettings({ max_output_tokens: Number(event.target.value) })
                    }
                  />
                </div>
                <div className="col-span-full flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <Label>{t("supportsMultimodal")}</Label>
                    <p className="text-muted-foreground text-xs">{t("supportsMultimodalDesc")}</p>
                  </div>
                  <Switch
                    checked={state.modelInput.capabilities?.vision ?? false}
                    onCheckedChange={(checked) =>
                      state.setModelInput({
                        ...state.modelInput,
                        capabilities: { ...state.modelInput.capabilities!, vision: checked },
                      })
                    }
                  />
                </div>
              </>
            ) : (
              <>
                <div>
                  <Label>{t("dimensions")}</Label>
                  <Input value="1536" disabled />
                </div>
                <div>
                  <Label>{t("maxBatchSize")}</Label>
                  <Input
                    type="number"
                    value={state.modelInput.settings.max_batch_size}
                    onChange={(event) =>
                      state.setModelInput({
                        ...state.modelInput,
                        settings: {
                          kind: "embedding",
                          dimensions: 1536,
                          max_batch_size: Number(event.target.value),
                        },
                      })
                    }
                  />
                </div>
              </>
            )}
            {isAdmin ? (
              <div>
                <Label>{t("visibility")}</Label>
                <Select
                  value={state.modelInput.visibility}
                  onValueChange={(value) =>
                    state.setModelInput({
                      ...state.modelInput,
                      visibility: value as ResourceVisibility,
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">{t("visibilityPrivate")}</SelectItem>
                    <SelectItem value="global">{t("visibilityGlobal")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => state.setModelDialogOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button disabled={state.saving} onClick={() => void state.saveModel()}>
              {state.saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
