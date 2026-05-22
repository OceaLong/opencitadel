'use client'

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Plus, Star, Trash2 } from 'lucide-react'
import { modelsApi } from '@/lib/api/models'
import type { LLMModel, LLMProvider, CreateLLMModelParams, ModelCapabilities } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'

/** 当前已完整实现、可用于调用的 Provider */
export const SUPPORTED_PROVIDERS: { value: LLMProvider; label: string }[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'ollama', label: 'Ollama' },
  { value: 'azure', label: 'Azure OpenAI' },
]

const defaultCapabilities: ModelCapabilities = {
  vision: false,
  vision_with_tools: true,
  max_image_bytes: 5 * 1024 * 1024,
  max_images_per_request: 8,
  image_encoding: 'data_url',
}

const emptyForm: CreateLLMModelParams = {
  display_name: '',
  provider: 'openai',
  base_url: 'https://api.openai.com/v1',
  api_key: '',
  model_name: 'gpt-4o',
  temperature: 0.7,
  max_tokens: 8192,
  capabilities: defaultCapabilities,
  supports_multimodal: false,
}

type Props = {
  embedded?: boolean
}

export function ModelsSettings({ embedded = false }: Props) {
  const [models, setModels] = useState<LLMModel[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<LLMModel | null>(null)
  const [form, setForm] = useState<CreateLLMModelParams>(emptyForm)
  const [saving, setSaving] = useState(false)
  const [probingId, setProbingId] = useState<string | null>(null)
  const [probeStatus, setProbeStatus] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await modelsApi.list()
      setModels(data.models)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    setForm(emptyForm)
    setDialogOpen(true)
  }

  const openEdit = (m: LLMModel) => {
    setEditing(m)
    setForm({
      display_name: m.display_name,
      provider: m.provider,
      base_url: m.base_url,
      api_key: '',
      model_name: m.model_name,
      temperature: m.temperature,
      max_tokens: m.max_tokens,
      capabilities: m.capabilities ?? {
        ...defaultCapabilities,
        vision: m.supports_multimodal ?? false,
      },
      supports_multimodal: m.supports_multimodal ?? false,
      is_default: m.is_default,
    })
    setDialogOpen(true)
  }

  const handleSave = async () => {
    if (!form.display_name.trim() || !form.model_name.trim() || !form.base_url.trim()) {
      toast.error('请填写显示名称、模型名和 Base URL')
      return
    }
    if (!editing && !form.api_key?.trim()) {
      toast.error('新建模型时必须填写 API Key')
      return
    }
    setSaving(true)
    const payload = {
      ...form,
      supports_multimodal: form.capabilities?.vision ?? form.supports_multimodal ?? false,
      capabilities: {
        ...defaultCapabilities,
        ...form.capabilities,
        vision: form.capabilities?.vision ?? form.supports_multimodal ?? false,
      },
    }
    try {
      if (editing) {
        await modelsApi.update(editing.id, payload)
        toast.success('模型已更新')
      } else {
        await modelsApi.create(payload)
        toast.success('模型已创建')
      }
      setDialogOpen(false)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await modelsApi.delete(id)
      toast.success('已删除')
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  const handleSetDefault = async (id: string) => {
    try {
      await modelsApi.setDefault(id)
      toast.success('已设为默认')
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败')
    }
  }

  const handleProbe = async (id: string) => {
    setProbingId(id)
    try {
      const result = await modelsApi.probeMultimodal(id)
      setProbeStatus((prev) => ({ ...prev, [id]: result.status }))
      if (result.status === 'ok') {
        toast.success(result.message || '多模态探测成功')
      } else {
        toast.info(result.message || '多模态探测完成')
      }
    } catch (e) {
      setProbeStatus((prev) => ({ ...prev, [id]: 'error' }))
      toast.error(e instanceof Error ? e.message : '探测失败')
    } finally {
      setProbingId(null)
    }
  }

  return (
    <div className={embedded ? 'w-full px-1' : 'max-w-4xl'}>
      <div className={`flex justify-between items-center ${embedded ? 'mb-4' : 'mb-6'}`}>
        <div>
          <h2 className={embedded ? 'text-lg font-bold text-gray-700' : 'text-2xl font-bold'}>
            模型管理
          </h2>
          <p className="text-muted-foreground text-sm mt-1">
            全局新增多 Provider 模型，会话级可选择使用
          </p>
        </div>
        <Button size={embedded ? 'xs' : 'default'} onClick={openCreate}>
          <Plus className="size-4 mr-1" />
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
            <Card key={m.id}>
              <CardHeader className="pb-2">
                <div className="flex justify-between items-start gap-2">
                  <div className="min-w-0">
                    <CardTitle className="text-base flex items-center gap-2 flex-wrap">
                      {m.display_name}
                      {m.is_default && <Badge variant="secondary">默认</Badge>}
                      {m.supports_multimodal && (
                        <Badge variant="outline">多模态</Badge>
                      )}
                      {probeStatus[m.id] === 'ok' && (
                        <Badge variant="outline" className="text-green-600">已校验</Badge>
                      )}
                      {probeStatus[m.id] === 'error' && (
                        <Badge variant="outline" className="text-red-600">探测失败</Badge>
                      )}
                      {!SUPPORTED_PROVIDERS.some((p) => p.value === m.provider) && (
                        <Badge variant="outline" className="text-amber-600">未实现</Badge>
                      )}
                    </CardTitle>
                    <CardDescription>
                      {m.provider} · {m.model_name}
                    </CardDescription>
                  </div>
                  <div className="flex gap-1 shrink-0">
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
                        {probingId === m.id ? '探测中...' : '测试多模态'}
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => openEdit(m)}>
                      编辑
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => handleDelete(m.id)}>
                      <Trash2 className="size-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground truncate">
                {m.base_url}
              </CardContent>
            </Card>
          ))}
          {models.length === 0 && (
            <p className="text-center text-muted-foreground py-8 text-sm">暂无模型，请先新增</p>
          )}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑模型' : '新增模型'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <Label>显示名称</Label>
              <Input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </div>
            <div>
              <Label>Provider</Label>
              <Select
                value={form.provider}
                onValueChange={(v) => setForm({ ...form, provider: v as LLMProvider })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SUPPORTED_PROVIDERS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Base URL</Label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </div>
            <div>
              <Label>API Key {editing && '(留空不更新)'}</Label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </div>
            <div>
              <Label>Model Name</Label>
              <Input
                value={form.model_name}
                onChange={(e) => setForm({ ...form, model_name: e.target.value })}
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border p-3">
              <div>
                <Label>支持多模态理解</Label>
                <p className="text-xs text-muted-foreground mt-1">
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
              <div className="grid grid-cols-2 gap-4 rounded-lg border p-3">
                <div>
                  <Label>单图最大字节</Label>
                  <Input
                    type="number"
                    value={form.capabilities?.max_image_bytes ?? defaultCapabilities.max_image_bytes}
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
                <div>
                  <Label>单次最多图片数</Label>
                  <Input
                    type="number"
                    value={form.capabilities?.max_images_per_request ?? defaultCapabilities.max_images_per_request}
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
              <div>
                <Label>Temperature</Label>
                <Input
                  type="number"
                  step="0.1"
                  value={form.temperature}
                  onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })}
                />
              </div>
              <div>
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
            <Button variant="outline" onClick={() => setDialogOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin mr-1" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
