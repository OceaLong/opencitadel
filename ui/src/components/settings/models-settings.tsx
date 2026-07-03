"use client";

import { Loader2, Plus, Star, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

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
  defaultCapabilities,
  SUPPORTED_PROVIDERS,
  useModelsSettings,
} from "@/hooks/use-models-settings";
import { useEndpointsSettings } from "@/hooks/use-endpoints-settings";
import { configApi } from "@/lib/api/config";
import type { LLMEndpoint, LLMModel, LLMProvider } from "@/lib/api/types";

type Props = {
  embedded?: boolean;
  isAdmin?: boolean;
  userId?: string;
};

function canManageResource(
  resource: { visibility?: string; owner_user_id?: string | null },
  isAdmin: boolean,
  userId?: string,
) {
  if (isAdmin) return true;
  return resource.visibility === "private" && resource.owner_user_id === userId;
}

export function ModelsSettings({ embedded = false, isAdmin = false, userId }: Props) {
  const endpointsState = useEndpointsSettings();
  const modelsState = useModelsSettings(endpointsState.reload);
  const tNav = useTranslations("settingsNav");
  const t = useTranslations("settingsModels");
  const tCommon = useTranslations("common");
  const [quotaFallbackEnabled, setQuotaFallbackEnabled] = useState(true);
  const [resiliencePayload, setResiliencePayload] = useState<Record<string, unknown> | null>(null);
  const [resilienceLoading, setResilienceLoading] = useState(true);
  const [resilienceSaving, setResilienceSaving] = useState(false);

  const loadResilienceConfig = useCallback(async () => {
    setResilienceLoading(true);
    try {
      const data = await configApi.getSection<Record<string, unknown>>("model_resilience");
      setResiliencePayload(data ?? {});
      setQuotaFallbackEnabled(data?.fallback_on_quota_exceeded !== false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("quotaFallbackLoadFailed"));
    } finally {
      setResilienceLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadResilienceConfig();
  }, [loadResilienceConfig]);

  const handleQuotaFallbackChange = async (checked: boolean) => {
    const previous = quotaFallbackEnabled;
    setQuotaFallbackEnabled(checked);
    setResilienceSaving(true);
    try {
      const base = resiliencePayload ?? (await configApi.getSection<Record<string, unknown>>("model_resilience"));
      const payload = { ...base, fallback_on_quota_exceeded: checked };
      const updated = await configApi.updateSection("model_resilience", payload);
      setResiliencePayload(updated);
      setQuotaFallbackEnabled(updated.fallback_on_quota_exceeded !== false);
      toast.success(t("quotaFallbackSaved"));
    } catch (err) {
      setQuotaFallbackEnabled(previous);
      toast.error(err instanceof Error ? err.message : t("quotaFallbackSaveFailed"));
    } finally {
      setResilienceSaving(false);
    }
  };

  const modelsByEndpoint = useMemo(() => {
    const grouped = new Map<string, LLMModel[]>();
    for (const model of modelsState.models) {
      const list = grouped.get(model.endpoint_id) ?? [];
      list.push(model);
      grouped.set(model.endpoint_id, list);
    }
    return grouped;
  }, [modelsState.models]);

  const loading = endpointsState.loading || modelsState.loading;

  const handleAddModel = () => {
    if (endpointsState.endpoints.length === 1) {
      modelsState.openCreate(endpointsState.endpoints[0].id);
      return;
    }
    if (endpointsState.endpoints.length === 0) {
      endpointsState.openCreate();
      return;
    }
    modelsState.openCreate(endpointsState.endpoints[0].id);
  };

  return (
    <div className={embedded ? "w-full px-1" : "max-w-4xl"}>
      <div className={`flex items-center justify-between ${embedded ? "mb-4" : "mb-6"}`}>
        <div>
          <h2
            className={
              embedded
                ? "text-foreground text-lg font-semibold"
                : "text-2xl font-semibold tracking-tight"
            }
          >
            {tNav("models")}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
        </div>
        <div className="flex gap-2">
          <Button size={embedded ? "xs" : "default"} variant="outline" onClick={endpointsState.openCreate}>
            <Plus className="mr-1 size-4" />
            {t("addEndpoint")}
          </Button>
          <Button size={embedded ? "xs" : "default"} onClick={handleAddModel}>
            <Plus className="mr-1 size-4" />
            {t("addModel")}
          </Button>
        </div>
      </div>

      <div className="border-border/70 bg-muted/20 mb-4 flex items-center justify-between rounded-xl border p-3">
        <div className="pr-4">
          <p className="text-sm font-medium">{t("quotaFallbackLabel")}</p>
          <p className="text-muted-foreground mt-1 text-xs">{t("quotaFallbackDesc")}</p>
        </div>
        {resilienceLoading ? (
          <Loader2 className="size-4 shrink-0 animate-spin" />
        ) : (
          <Switch
            checked={quotaFallbackEnabled}
            disabled={resilienceSaving}
            onCheckedChange={(checked) => void handleQuotaFallbackChange(checked)}
          />
        )}
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : (
        <div className="grid gap-4">
          {endpointsState.endpoints.map((endpoint) => (
            <EndpointGroup
              key={endpoint.id}
              endpoint={endpoint}
              models={modelsByEndpoint.get(endpoint.id) ?? []}
              isAdmin={isAdmin}
              userId={userId}
              probingId={modelsState.probingId}
              probeStatus={modelsState.probeStatus}
              onEditEndpoint={endpointsState.openEdit}
              onDeleteEndpoint={endpointsState.handleDelete}
              onAddModel={modelsState.openCreate}
              onEditModel={modelsState.openEdit}
              onDeleteModel={modelsState.handleDelete}
              onSetDefault={modelsState.handleSetDefault}
              onProbe={modelsState.handleProbe}
            />
          ))}
          {endpointsState.endpoints.length === 0 && (
            <p className="text-muted-foreground py-8 text-center text-sm">{t("noEndpoints")}</p>
          )}
        </div>
      )}

      <Dialog open={endpointsState.dialogOpen} onOpenChange={endpointsState.setDialogOpen}>
        <DialogContent className="max-w-lg shadow-[var(--shadow-panel)]">
          <DialogHeader>
            <DialogTitle>{endpointsState.editing ? t("editEndpoint") : t("addEndpoint")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t("endpointDisplayName")}</Label>
              <Input
                value={endpointsState.form.display_name}
                onChange={(e) =>
                  endpointsState.setForm({ ...endpointsState.form, display_name: e.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label>{t("provider")}</Label>
              <Select
                value={endpointsState.form.provider}
                onValueChange={(v) =>
                  endpointsState.setForm({ ...endpointsState.form, provider: v as LLMProvider })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SUPPORTED_PROVIDERS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{t("baseUrl")}</Label>
              <Input
                value={endpointsState.form.base_url}
                onChange={(e) =>
                  endpointsState.setForm({ ...endpointsState.form, base_url: e.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label>{endpointsState.editing ? t("apiKeyLeaveBlank") : t("apiKey")}</Label>
              <Input
                type="password"
                value={endpointsState.form.api_key}
                onChange={(e) =>
                  endpointsState.setForm({ ...endpointsState.form, api_key: e.target.value })
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => endpointsState.setDialogOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button onClick={endpointsState.handleSave} disabled={endpointsState.saving}>
              {endpointsState.saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={modelsState.dialogOpen} onOpenChange={modelsState.setDialogOpen}>
        <DialogContent className="max-w-lg shadow-[var(--shadow-panel)]">
          <DialogHeader>
            <DialogTitle>{modelsState.editing ? t("editModel") : t("addModel")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            {(modelsState.editing ? endpointsState.endpoints.length > 0 : endpointsState.endpoints.length > 1) && (
              <div className="space-y-2">
                <Label>{t("endpoint")}</Label>
                <Select
                  value={modelsState.form.endpoint_id}
                  onValueChange={(v) => modelsState.setForm({ ...modelsState.form, endpoint_id: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {endpointsState.endpoints.map((endpoint) => (
                      <SelectItem key={endpoint.id} value={endpoint.id}>
                        {endpoint.display_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="space-y-2">
              <Label>{t("displayName")}</Label>
              <Input
                value={modelsState.form.display_name}
                onChange={(e) => modelsState.updateDisplayName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("modelName")}</Label>
              <Input
                value={modelsState.form.model_name}
                onChange={(e) => modelsState.updateModelName(e.target.value)}
              />
            </div>
            <div className="border-border/70 bg-muted/20 flex items-center justify-between rounded-xl border p-3">
              <div>
                <Label>{t("supportsMultimodal")}</Label>
                <p className="text-muted-foreground mt-1 text-xs">{t("supportsMultimodalDesc")}</p>
              </div>
              <Switch
                checked={modelsState.form.capabilities?.vision ?? modelsState.form.supports_multimodal ?? false}
                onCheckedChange={(checked) =>
                  modelsState.setForm({
                    ...modelsState.form,
                    supports_multimodal: checked,
                    capabilities: {
                      ...defaultCapabilities,
                      ...modelsState.form.capabilities,
                      vision: checked,
                    },
                  })
                }
              />
            </div>
            {(modelsState.form.capabilities?.vision ?? modelsState.form.supports_multimodal) && (
              <div className="border-border/70 bg-muted/20 grid grid-cols-2 gap-4 rounded-xl border p-3">
                <div className="space-y-2">
                  <Label>{t("maxImageBytes")}</Label>
                  <Input
                    type="number"
                    value={
                      modelsState.form.capabilities?.max_image_bytes ?? defaultCapabilities.max_image_bytes
                    }
                    onChange={(e) =>
                      modelsState.setForm({
                        ...modelsState.form,
                        capabilities: {
                          ...defaultCapabilities,
                          ...modelsState.form.capabilities,
                          vision: true,
                          max_image_bytes: Number(e.target.value),
                        },
                      })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("maxImagesPerRequest")}</Label>
                  <Input
                    type="number"
                    value={
                      modelsState.form.capabilities?.max_images_per_request ??
                      defaultCapabilities.max_images_per_request
                    }
                    onChange={(e) =>
                      modelsState.setForm({
                        ...modelsState.form,
                        capabilities: {
                          ...defaultCapabilities,
                          ...modelsState.form.capabilities,
                          vision: true,
                          max_images_per_request: Number(e.target.value),
                        },
                      })
                    }
                  />
                </div>
                <div className="col-span-2 flex items-center justify-between">
                  <Label>{t("visionWithTools")}</Label>
                  <Switch
                    checked={modelsState.form.capabilities?.vision_with_tools ?? true}
                    onCheckedChange={(checked) =>
                      modelsState.setForm({
                        ...modelsState.form,
                        capabilities: {
                          ...defaultCapabilities,
                          ...modelsState.form.capabilities,
                          vision: true,
                          vision_with_tools: checked,
                        },
                      })
                    }
                  />
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("temperature")}</Label>
                <Input
                  type="number"
                  step="0.1"
                  value={modelsState.form.temperature}
                  onChange={(e) =>
                    modelsState.setForm({ ...modelsState.form, temperature: Number(e.target.value) })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label>{t("maxTokens")}</Label>
                <Input
                  type="number"
                  value={modelsState.form.max_tokens}
                  onChange={(e) =>
                    modelsState.setForm({ ...modelsState.form, max_tokens: Number(e.target.value) })
                  }
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => modelsState.setDialogOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button onClick={modelsState.handleSave} disabled={modelsState.saving}>
              {modelsState.saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

type EndpointGroupProps = {
  endpoint: LLMEndpoint;
  models: LLMModel[];
  isAdmin: boolean;
  userId?: string;
  probingId: string | null;
  probeStatus: Record<string, string>;
  onEditEndpoint: (endpoint: LLMEndpoint) => void;
  onDeleteEndpoint: (id: string) => void;
  onAddModel: (endpointId: string) => void;
  onEditModel: (model: LLMModel) => void;
  onDeleteModel: (id: string) => void;
  onSetDefault: (id: string) => void;
  onProbe: (id: string) => void;
};

function EndpointGroup({
  endpoint,
  models,
  isAdmin,
  userId,
  probingId,
  probeStatus,
  onEditEndpoint,
  onDeleteEndpoint,
  onAddModel,
  onEditModel,
  onDeleteModel,
  onSetDefault,
  onProbe,
}: EndpointGroupProps) {
  const t = useTranslations("settingsModels");
  const tCommon = useTranslations("common");
  const canManageEndpoint = canManageResource(endpoint, isAdmin, userId);

  return (
    <Card className="hover:border-border transition-all hover:shadow-[var(--shadow-card-hover)]">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base">{endpoint.display_name}</CardTitle>
            <CardDescription className="mt-1">
              {endpoint.provider} · {endpoint.base_url}
            </CardDescription>
          </div>
          {canManageEndpoint ? (
            <div className="flex shrink-0 gap-1">
              <Button variant="ghost" size="sm" onClick={() => onAddModel(endpoint.id)}>
                <Plus className="mr-1 size-3.5" />
                {t("addModelUnderEndpoint")}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => onEditEndpoint(endpoint)}>
                {tCommon("edit")}
              </Button>
              <Button variant="ghost" size="icon" onClick={() => onDeleteEndpoint(endpoint.id)}>
                <Trash2 className="text-destructive size-4" />
              </Button>
            </div>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        {models.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("noModelsUnderEndpoint")}</p>
        ) : (
          models.map((model) => (
            <div
              key={model.id}
              className="border-border/70 flex items-start justify-between gap-2 rounded-lg border px-3 py-2"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  {model.display_name}
                  {model.is_default && <Badge variant="secondary">{tCommon("default")}</Badge>}
                  {model.supports_multimodal && <Badge variant="outline">{t("multimodal")}</Badge>}
                  {probeStatus[model.id] === "ok" && (
                    <Badge variant="outline" className="text-green-600">
                      {t("verified")}
                    </Badge>
                  )}
                  {probeStatus[model.id] === "error" && (
                    <Badge variant="outline" className="text-red-600">
                      {t("probeFailed")}
                    </Badge>
                  )}
                  {!SUPPORTED_PROVIDERS.some((p) => p.value === model.provider) && (
                    <Badge variant="outline" className="text-amber-600">
                      {t("notImplemented")}
                    </Badge>
                  )}
                </div>
                <p className="text-muted-foreground mt-1 text-xs">{model.model_name}</p>
              </div>
              <div className="flex shrink-0 gap-1">
                {isAdmin && !model.is_default && (
                  <Button variant="ghost" size="icon" onClick={() => onSetDefault(model.id)}>
                    <Star className="size-4" />
                  </Button>
                )}
                {model.supports_multimodal && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={probingId === model.id}
                    onClick={() => onProbe(model.id)}
                  >
                    {probingId === model.id ? t("probing") : t("testMultimodal")}
                  </Button>
                )}
                {canManageResource(model, isAdmin, userId) ? (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => onEditModel(model)}>
                      {tCommon("edit")}
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => onDeleteModel(model.id)}>
                      <Trash2 className="text-destructive size-4" />
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
