'use client'

import { useMemo } from 'react'
import { Bug } from 'lucide-react'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import type { SSEEventData } from '@/lib/api/types'
import { extractDebugItems } from '@/lib/session-events'

type Props = {
  events: SSEEventData[]
  compact?: boolean
}

function formatPayload(payload: Record<string, unknown>): string {
  try {
    return JSON.stringify(payload, null, 2)
  } catch {
    return String(payload)
  }
}

export function SessionDebugSheet({ events, compact }: Props) {
  const debugItems = useMemo(() => extractDebugItems(events), [events])

  if (debugItems.length === 0) {
    return null
  }

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size={compact ? 'icon' : 'sm'}
          className={compact ? 'size-8' : undefined}
          title="调试事件"
        >
          <Bug className="size-4" />
          {!compact && <span className="ml-1">调试</span>}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>调试事件</SheetTitle>
          <SheetDescription>
            内部 planner 输出、reasoning 与 tool args，不会显示在普通聊天气泡中。
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100vh-8rem)] mt-4 pr-3">
          <div className="flex flex-col gap-3">
            {debugItems.map((item, index) => (
              <div
                key={`${item.item_type}-${index}`}
                className="rounded-lg border border-border/70 bg-muted/30 overflow-hidden"
              >
                <div className="flex items-center gap-2 px-3 py-2 border-b border-border/60 bg-muted/50">
                  <Badge variant="outline" className="text-[10px] font-mono">
                    {item.item_type}
                  </Badge>
                </div>
                <pre className="p-3 text-xs whitespace-pre-wrap break-words font-mono text-muted-foreground max-h-64 overflow-auto">
                  {formatPayload(item.payload)}
                </pre>
              </div>
            ))}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
