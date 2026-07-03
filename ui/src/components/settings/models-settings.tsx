"use client";

import { Loader2, Plus, Star, Trash2 } from "lucide-react";
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
import type { LLMProvider } from "@/lib/api/types";

type Props = {
  embedded?: boolean;
  isAdmin?: boolean;
  userId?: string;
};

function canManageModel(model: { visibility?: string; owner_user_id?: string | null }, isAdmin: boolean, userId?: string) {
  if (isAdmin) return true;
  return model.visibility === "private" && model.owner_user_id === userId;
}

export function ModelsSettings({ embedded = false, isAdmin = false, userId }: Props) {
  const {
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
  } = useModelsSettings();
  const tNav = useTranslations("settingsNav");
  const t = useTranslations("settingsModels");
  const tCommon = useTranslations("common");

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
        <Button size={embedded ? "xs" : "default"} onClick={openCreate}>
          <Plus className="mr-1 size-4" />
          {t("addModel")}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : (
        <div className="grid gap-3">
          {models.map((m) => (
            <Card
              key={m.id}
              className="hover:border-border transition-all hover:shadow-[var(--shadow-card-hover)]"
            >
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                      {m.display_name}
                      {m.is_default && <Badge variant="secondary">{tCommon("default")}</Badge>}
                      {m.supports_multimodal && <Badge variant="outline">{t("multimodal")}</Badge>}
                      {probeStatus[m.id] === "ok" && (
                        <Badge variant="outline" className="text-green-600">
                          {t("verified")}
                        </Badge>
                      )}
                      {probeStatus[m.id] === "error" && (
                        <Badge variant="outline" className="text-red-600">
                          {t("probeFailed")}
                        </Badge>
                      )}
                      {!SUPPORTED_PROVIDERS.some((p) => p.value === m.provider) && (
                        <Badge variant="outline" className="text-amber-600">
                          {t("notImplemented")}
                        </Badge>
                      )}
                    </CardTitle>
                    <CardDescription>
                      {m.provider} · {m.model_name}
                    </CardDescription>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {isAdmin && !m.is_default && (
                      <Button variant="ghost" size="icon" onClick={() => handleSetDefault(m.id)}>
                        <Star className="size-4" />
                      </Button>
                    )}
                    {m.supports_multimodal && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={probingId === m.id}
                        onClick={() => handleProbe(m.id)}
                      >
                        {probingId === m.id ? t("probing") : t("testMultimodal")}
                      </Button>
                    )}
                    {canManageModel(m, isAdmin, userId) ? (
                      <>
                    <Button variant="ghost" size="sm" onClick={() => openEdit(m)}>
                      {tCommon("edit")}
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => handleDelete(m.id)}>
                      <Trash2 className="text-destructive size-4" />
                    </Button>
                      </>
                    ) : null}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="text-muted-foreground truncate pt-0 text-xs">
                {m.base_url}
              </CardContent>
            </Card>
          ))}
          {models.length === 0 && (
            <p className="text-muted-foreground py-8 text-center text-sm">{t("noModels")}</p>
          )}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg shadow-[var(--shadow-panel)]">
          <DialogHeader>
            <DialogTitle>{editing ? t("editModel") : t("addModel")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t("displayName")}</Label>
              <Input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Provider</Label>
              <Select
                value={form.provider}
                onValueChange={(v) => setForm({ ...form, provider: v as LLMProvider })}
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
              <Label>Base URL</Label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>{editing ? t("apiKeyLeaveBlank") : "API Key"}</Label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Model Name</Label>
              <Input
                value={form.model_name}
                onChange={(e) => setForm({ ...form, model_name: e.target.value })}
              />
            </div>
            <div className="border-border/70 bg-muted/20 flex items-center justify-between rounded-xl border p-3">
              <div>
                <Label>{t("supportsMultimodal")}</Label>
                <p className="text-muted-foreground mt-1 text-xs">{t("supportsMultimodalDesc")}</p>
              </div>
              <Switch
                checked={form.capabilities?.vision ?? form.supports_multimodal ?? false}
                onCheckedChange={(checked) =>
                  setForm({
                    ...form,
                    supports_multimodal: checked,
                    capabilities: {
                      ...defaultCapabilities,
                      ...form.capabilities,
                      vision: checked,
                    },
                  })
                }
              />
            </div>
            {(form.capabilities?.vision ?? form.supports_multimodal) && (
              <div className="border-border/70 bg-muted/20 grid grid-cols-2 gap-4 rounded-xl border p-3">
                <div className="space-y-2">
                  <Label>{t("maxImageBytes")}</Label>
                  <Input
                    type="number"
                    value={
                      form.capabilities?.max_image_bytes ?? defaultCapabilities.max_image_bytes
                    }
                    onChange={(e) =>
                      setForm({
                        ...form,
                        capabilities: {
                          ...defaultCapabilities,
                          ...form.capabilities,
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
                      form.capabilities?.max_images_per_request ??
                      defaultCapabilities.max_images_per_request
                    }
                    onChange={(e) =>
                      setForm({
                        ...form,
                        capabilities: {
                          ...defaultCapabilities,
                          ...form.capabilities,
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
                    checked={form.capabilities?.vision_with_tools ?? true}
                    onCheckedChange={(checked) =>
                      setForm({
                        ...form,
                        capabilities: {
                          ...defaultCapabilities,
                          ...form.capabilities,
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
                <Label>Temperature</Label>
                <Input
                  type="number"
                  step="0.1"
                  value={form.temperature}
                  onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-2">
                <Label>Max Tokens</Label>
                <Input
                  type="number"
                  value={form.max_tokens}
                  onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
