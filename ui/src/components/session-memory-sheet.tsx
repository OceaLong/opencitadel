'use client'

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Brain, Copy, Loader2, Trash2 } from 'lucide-react'
import { memoryApi } from '@/lib/api/memory'
import type { SessionMemoryData } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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
import { cn } from '@/lib/utils'

type Props = {
  sessionId: string
  editable?: boolean
  /** 自定义触发按钮，不传则使用默认样式 */
  trigger?: React.ReactNode
  /** 紧凑图标按钮模式（用于会话头部） */
  compact?: boolean
}

function formatMessageContent(msg: Record<string, unknown>): string {
  if (typeof msg.content === 'string') {
    return msg.content.trim()
  }
  return JSON.stringify(msg, null, 2).trim()
}

function MemoryMessageCard({
  index,
  msg,
  editable,
  onDelete,
}: {
  index: number
  msg: Record<string, unknown>
  editable: boolean
  onDelete: () => void
}) {
  const content = formatMessageContent(msg)
  const displayContent = content.slice(0, 2000) + (content.length > 2000 ? '\n…' : '')
  const role = String(msg.role ?? 'unknown')

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      toast.success('已复制')
    } catch {
      toast.error('复制失败')
    }
  }

  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 overflow-hidden shadow-sm">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border/60 bg-muted/50">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {role}
          </span>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono">
            #{index + 1}
          </Badge>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={handleCopy}
            title="复制"
          >
            <Copy className="size-3.5" />
          </Button>
          {editable && (
            <Button
              size="icon"
              variant="ghost"
              className="size-7 text-destructive hover:text-destructive"
              onClick={onDelete}
              title="删除"
            >
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
      <pre className="p-3 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words max-h-48 overflow-auto text-foreground/90">
        {displayContent || '(empty)'}
      </pre>
    </div>
  )
}

export function SessionMemorySheet({
  sessionId,
  editable = true,
  trigger,
  compact = false,
}: Props) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<SessionMemoryData | null>(null)
  const [loading, setLoading] = useState(false)
  const [mounted, setMounted] = useState(false)

  const plannerCount = data?.planner?.length ?? 0
  const reactCount = data?.react?.length ?? 0
  const messageCount = plannerCount + reactCount

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
    setMounted(true)
    load().catch(() => {})
  }, [load])

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
    <div className="space-y-3 pr-2">
      {editable && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => handleCompact(agent)}>
            压缩
          </Button>
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => handleClear(agent)}>
            清空
          </Button>
        </div>
      )}
      {messages.map((msg, i) => (
        <MemoryMessageCard
          key={i}
          index={i}
          msg={msg}
          editable={editable}
          onDelete={() => handleDeleteMsg(agent, i)}
        />
      ))}
      {messages.length === 0 && (
        <p className="text-muted-foreground text-sm py-6 text-center">暂无消息</p>
      )}
    </div>
  )

  const defaultTrigger = compact ? (
    <Button
      variant="ghost"
      size="icon-sm"
      className="cursor-pointer flex-shrink-0"
      title="会话记忆"
    >
      <Brain />
    </Button>
  ) : (
    <Button variant="ghost" size="sm" className="h-8 gap-1">
      <Brain className="size-4" />
      记忆
    </Button>
  )

  if (!mounted) {
    return trigger ?? defaultTrigger
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {trigger ?? defaultTrigger}
      </SheetTrigger>
      <SheetContent className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            会话内存
            {!loading && (
              <Badge variant="secondary" className="font-normal text-xs">
                共 {messageCount} 条
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            查看与编辑 Agent 短期上下文（Tier 1）
            {!loading && messageCount > 0 && (
              <span className="block mt-1 text-xs">
                Planner {plannerCount} 条 · ReAct {reactCount} 条
              </span>
            )}
          </SheetDescription>
        </SheetHeader>
        {loading ? (
          <div className="flex justify-center py-12"><Loader2 className="size-6 animate-spin" /></div>
        ) : data ? (
          <Tabs defaultValue="planner" className="mt-4">
            <TabsList>
              <TabsTrigger value="planner">
                Planner
                <span className={cn('ml-1 text-xs text-muted-foreground')}>
                  ({plannerCount})
                </span>
              </TabsTrigger>
              <TabsTrigger value="react">
                ReAct
                <span className={cn('ml-1 text-xs text-muted-foreground')}>
                  ({reactCount})
                </span>
              </TabsTrigger>
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
