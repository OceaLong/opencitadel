'use client'

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react'
import { memoryApi } from '@/lib/api/memory'
import type { MemoryEntry, MemoryScope } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

type MemoryForm = {
  title: string
  content: string
  tags: string
  scope: MemoryScope
  session_id: string
}

const emptyForm: MemoryForm = {
  title: '',
  content: '',
  tags: '',
  scope: 'global',
  session_id: '',
}

type Props = {
  embedded?: boolean
}

export function MemorySettings({ embedded = false }: Props) {
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [filterScope, setFilterScope] = useState<MemoryScope | 'all'>('all')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<MemoryEntry | null>(null)
  const [form, setForm] = useState<MemoryForm>(emptyForm)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await memoryApi.list(
        filterScope === 'all' ? {} : { scope: filterScope }
      )
      setEntries(data.entries)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [filterScope])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    setForm(emptyForm)
    setDialogOpen(true)
  }

  const openEdit = (entry: MemoryEntry) => {
    setEditing(entry)
    setForm({
      title: entry.title,
      content: entry.content,
      tags: entry.tags.join(', '),
      scope: entry.scope,
      session_id: entry.session_id || '',
    })
    setDialogOpen(true)
  }

  const handleSave = async () => {
    if (form.scope === 'session' && !form.session_id.trim()) {
      toast.error('会话作用域必须填写 session_id')
      return
    }
    setSaving(true)
    const payload = {
      title: form.title,
      content: form.content,
      tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
      scope: form.scope,
      session_id: form.scope === 'session' ? form.session_id.trim() : undefined,
    }
    try {
      if (editing) {
        await memoryApi.update(editing.id, payload)
        toast.success('记忆已更新')
      } else {
        await memoryApi.create(payload)
        toast.success('记忆已创建')
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
      await memoryApi.delete(id)
      toast.success('已删除')
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  return (
    <div className={embedded ? 'w-full px-1' : 'max-w-4xl'}>
      <div className={`flex justify-between items-center gap-2 flex-wrap ${embedded ? 'mb-4' : 'mb-6'}`}>
        <div>
          <h2 className={embedded ? 'text-lg font-bold text-gray-700' : 'text-2xl font-bold'}>
            长期记忆
          </h2>
          <p className="text-muted-foreground text-sm mt-1">
            跨会话记忆管理，任务开始时自动召回注入 Agent
          </p>
        </div>
        <div className="flex gap-2">
          <Select value={filterScope} onValueChange={(v) => setFilterScope(v as MemoryScope | 'all')}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="global">全局</SelectItem>
              <SelectItem value="session">会话</SelectItem>
            </SelectContent>
          </Select>
          <Button size={embedded ? 'xs' : 'default'} onClick={openCreate}>
            <Plus className="size-4 mr-1" />
            新增
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="size-6 animate-spin" /></div>
      ) : (
        <div className="space-y-3">
          {entries.map((e) => (
            <div
              key={e.id}
              className="group rounded-lg border border-border/70 bg-muted/20 overflow-hidden shadow-sm transition-colors hover:bg-muted/30"
            >
              <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-border/60 bg-muted/40">
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold text-foreground truncate">{e.title}</h3>
                  <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                      {e.scope}
                    </Badge>
                    {e.session_id && (
                      <Badge variant="outline" className="font-mono text-[10px] px-1.5 py-0">
                        {e.session_id.slice(0, 8)}…
                      </Badge>
                    )}
                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                      {e.source}
                    </Badge>
                    {e.use_count > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        使用 {e.use_count} 次
                      </span>
                    )}
                  </div>
                  {e.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {e.tags.map((tag) => (
                        <span
                          key={tag}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-background border border-border/60 text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex gap-0.5 shrink-0 opacity-80 group-hover:opacity-100">
                  <Button variant="ghost" size="icon" className="size-8" onClick={() => openEdit(e)}>
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="size-8" onClick={() => handleDelete(e.id)}>
                    <Trash2 className="size-3.5 text-destructive" />
                  </Button>
                </div>
              </div>
              <div className="px-4 py-3 text-sm text-muted-foreground whitespace-pre-wrap line-clamp-4 font-mono leading-relaxed">
                {e.content}
              </div>
            </div>
          ))}
          {entries.length === 0 && (
            <p className="text-center text-muted-foreground py-8 text-sm">暂无记忆条目</p>
          )}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? '编辑记忆' : '新增记忆'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <Label>标题</Label>
              <Input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
              />
            </div>
            <div>
              <Label>内容</Label>
              <Textarea
                rows={4}
                value={form.content}
                onChange={(e) => setForm({ ...form, content: e.target.value })}
              />
            </div>
            <div>
              <Label>标签（逗号分隔）</Label>
              <Input
                value={form.tags}
                onChange={(e) => setForm({ ...form, tags: e.target.value })}
              />
            </div>
            <div>
              <Label>作用域</Label>
              <Select
                value={form.scope}
                onValueChange={(v) =>
                  setForm({
                    ...form,
                    scope: v as MemoryScope,
                    session_id: v === 'session' ? form.session_id : '',
                  })
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="global">全局</SelectItem>
                  <SelectItem value="session">会话</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.scope === 'session' && (
              <div>
                <Label>Session ID</Label>
                <Input
                  value={form.session_id}
                  onChange={(e) => setForm({ ...form, session_id: e.target.value })}
                  placeholder="目标会话 UUID"
                />
              </div>
            )}
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
