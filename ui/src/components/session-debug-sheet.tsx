"use client";

import { useMemo, useState } from "react";
import { Bug } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import type { SSEEventData } from "@/lib/api/types";
import { extractDebugItems } from "@/lib/session-events";

type Props = {
  events: SSEEventData[];
  compact?: boolean;
  onOpen?: () => void;
};

function formatPayload(payload: Record<string, unknown>): string {
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

function getDebugDescription(type: string): string {
  if (type.includes("planner")) return "Planner";
  if (type.includes("reasoning")) return "Reasoning";
  if (type.includes("tool")) return "Tool Args";
  return "Debug";
}

export function SessionDebugSheet({ events, compact, onOpen }: Props) {
  const t = useTranslations("sessionDebug");
  const [open, setOpen] = useState(false);
  const debugItems = useMemo(() => extractDebugItems(events), [events]);

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) onOpen?.();
  };

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size={compact ? "icon" : "sm"}
          className={compact ? "size-8" : undefined}
          title={t("title")}
        >
          <Bug className="size-4" />
          {!compact && <span className="ml-1">{t("button")}</span>}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <ScrollArea className="mt-4 h-[calc(100vh-8rem)] pr-3">
          <div className="flex flex-col gap-3">
            {debugItems.length === 0 ? (
              <div className="text-muted-foreground border-border/70 bg-muted/30 rounded-lg border p-4 text-sm">
                {t("empty")}
              </div>
            ) : (
              debugItems.map((item, index) => (
                <div
                  key={`${item.item_type}-${index}`}
                  className="border-border/70 bg-muted/30 overflow-hidden rounded-lg border"
                >
                  <div className="border-border/60 bg-muted/50 flex items-center justify-between gap-2 border-b px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <Badge variant="outline" className="font-mono text-2xs">
                        {getDebugDescription(item.item_type)}
                      </Badge>
                      <span className="text-muted-foreground truncate font-mono text-xs">
                        {item.item_type}
                      </span>
                    </div>
                    {item.created_at && (
                      <span className="text-muted-foreground text-2xs">
                        {new Date(item.created_at * 1000).toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                  <pre className="text-muted-foreground max-h-64 overflow-auto p-3 font-mono text-xs break-words whitespace-pre-wrap">
                    {formatPayload(item.payload)}
                  </pre>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
