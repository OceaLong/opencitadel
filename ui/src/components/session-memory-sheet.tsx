'use client'

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Brain, Loader2, Trash2 } from 'lucide-react'
import { memoryApi } from '@/lib/api/memory'
import type { SessionMemoryData } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'

type Props = {
  sessionId: string
  editable?: boolean
}

export function SessionMemorySheet({ sessionId, editable = true }: Props) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<SessionMemoryData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const mem = await memoryApi.getSessionMemory(sessionId)
      setData(mem)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const handleCompact = async (agent: string) => {
    try {
      await memoryApi.compactSessionMemory(sessionId, agent)
      toast.success('已压缩')
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败')
    }
  }

  const handleClear = async (agent: string) => {
    try {
      await memoryApi.clearSessionMemory(sessionId, agent)
      toast.success('已清空')
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败')
    }
  }

  const handleDeleteMsg = async (agent: string, index: number) => {
    try {
      await memoryApi.deleteSessionMemoryMessage(sessionId, agent, index)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  const renderAgent = (agent: string, messages: Array<Record<string, unknown>>) => (
    <div className="space-y-2">
      {editable && (
        <div className="flex gap-2 mb-2">
          <Button size="sm" variant="outline" onClick={() => handleCompact(agent)}>压缩</Button>
          <Button size="sm" variant="outline" onClick={() => handleClear(agent)}>清空</Button>
        </div>
      )}
      {messages.map((msg, i) => (
        <div key={i} className="border rounded p-2 text-xs relative group">
          <span className="font-mono text-muted-foreground">[{String(msg.role)}]</span>
          <pre className="mt-1 whitespace-pre-wrap break-all max-h-32 overflow-auto">
            {typeof msg.content === 'string'
              ? msg.content.slice(0, 500)
              : JSON.stringify(msg, null, 2).slice(0, 500)}
          </pre>
          {editable && (
            <Button
              size="icon"
              variant="ghost"
              className="absolute top-1 right-1 size-6 opacity-0 group-hover:opacity-100"
              onClick={() => handleDeleteMsg(agent, i)}
            >
              <Trash2 className="size-3" />
            </Button>
          )}
        </div>
      ))}
      {messages.length === 0 && <p className="text-muted-foreground text-sm">暂无消息</p>}
    </div>
  )

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="sm" className="h-8 gap-1">
          <Brain className="size-4" />
          记忆
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>会话内存</SheetTitle>
          <SheetDescription>查看与编辑 Agent 短期上下文（Tier 1）</SheetDescription>
        </SheetHeader>
        {loading ? (
          <div className="flex justify-center py-12"><Loader2 className="size-6 animate-spin" /></div>
        ) : data ? (
          <Tabs defaultValue="planner" className="mt-4">
            <TabsList>
              <TabsTrigger value="planner">Planner</TabsTrigger>
              <TabsTrigger value="react">ReAct</TabsTrigger>
            </TabsList>
            <ScrollArea className="h-[calc(100vh-180px)] mt-4">
              <TabsContent value="planner">{renderAgent('planner', data.planner)}</TabsContent>
              <TabsContent value="react">{renderAgent('react', data.react)}</TabsContent>
            </ScrollArea>
          </Tabs>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
