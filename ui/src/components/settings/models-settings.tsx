"use client";

import { Loader2, Plus, Star, Trash2 } from "lucide-react";

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
};

export function ModelsSettings({ embedded = false }: Props) {
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
            模型管理
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            全局新增多 Provider 模型，会话级可选择使用
          </p>
        </div>
        <Button size={embedded ? "xs" : "default"} onClick={openCreate}>
          <Plus className="mr-1 size-4" />
          新增模型
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
                      {m.is_default && <Badge variant="secondary">默认</Badge>}
                      {m.supports_multimodal && <Badge variant="outline">多模态</Badge>}
                      {probeStatus[m.id] === "ok" && (
                        <Badge variant="outline" className="text-green-600">
                          已校验
                        </Badge>
                      )}
                      {probeStatus[m.id] === "error" && (
                        <Badge variant="outline" className="text-red-600">
                          探测失败
                        </Badge>
                      )}
                      {!SUPPORTED_PROVIDERS.some((p) => p.value === m.provider) && (
                        <Badge variant="outline" className="text-amber-600">
                          未实现
                        </Badge>
                      )}
                    </CardTitle>
                    <CardDescription>
                      {m.provider} · {m.model_name}
                    </CardDescription>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {!m.is_default && (
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
                        {probingId === m.id ? "探测中..." : "测试多模态"}
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => openEdit(m)}>
                      编辑
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => handleDelete(m.id)}>
                      <Trash2 className="text-destructive size-4" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="text-muted-foreground truncate pt-0 text-xs">
                {m.base_url}
              </CardContent>
            </Card>
          ))}
          {models.length === 0 && (
            <p className="text-muted-foreground py-8 text-center text-sm">暂无模型，请先新增</p>
          )}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg shadow-[var(--shadow-panel)]">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑模型" : "新增模型"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>显示名称</Label>
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
              <Label>API Key {editing && "(留空不更新)"}</Label>
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
                <Label>支持多模态理解</Label>
                <p className="text-muted-foreground mt-1 text-xs">
                  开启后可将用户图片与浏览器截图直接传给模型
                </p>
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
                  <Label>单图最大字节</Label>
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
                  <Label>单次最多图片数</Label>
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
                  <Label>携带工具时仍发送图片</Label>
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
              取消
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
