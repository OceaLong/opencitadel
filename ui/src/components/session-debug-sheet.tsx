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
import { extractDebugItems, extractSessionErrors } from "@/lib/session-events";

type Props = {
  events: SSEEventData[];
  includeDebug?: boolean;
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

export function SessionDebugSheet({ events, includeDebug = false, compact, onOpen }: Props) {
  const t = useTranslations("sessionDebug");
  const [open, setOpen] = useState(false);
  const errorItems = useMemo(() => extractSessionErrors(events), [events]);
  const debugItems = useMemo(
    () => (includeDebug ? extractDebugItems(events) : []),
    [events, includeDebug],
  );

  const getDebugDescription = (type: string): string => {
    if (type.includes("planner")) return t("typePlanner");
    if (type.includes("reasoning")) return t("typeReasoning");
    if (type.includes("tool")) return t("typeToolArgs");
    return t("typeDebug");
  };

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) onOpen?.();
  };

  const showGlobalEmpty = errorItems.length === 0 && debugItems.length === 0;

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size={compact ? "icon" : "sm"}
          className={compact ? "relative size-8" : "relative"}
          title={t("title")}
        >
          <Bug className="size-4" />
          {!compact && <span className="ml-1">{t("button")}</span>}
          {errorItems.length > 0 && (
            <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-medium text-white">
              {errorItems.length > 99 ? "99+" : errorItems.length}
            </span>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <ScrollArea className="mt-4 h-[calc(100vh-8rem)] pr-3">
          <div className="flex flex-col gap-4">
            {errorItems.length > 0 && (
              <section className="flex flex-col gap-2">
                <h3 className="text-foreground text-sm font-medium">
                  {t("errorsTitle", { count: errorItems.length })}
                </h3>
                <div className="flex flex-col gap-2">
                  {errorItems.map((item) => (
                    <div
                      key={item.id}
                      className="border-destructive/30 bg-destructive/5 overflow-hidden rounded-lg border"
                    >
                      <div className="border-destructive/20 bg-destructive/10 flex items-center justify-between gap-2 border-b px-3 py-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <Badge variant="outline" className="border-destructive/40 text-destructive text-2xs">
                            {item.source === "tool"
                              ? t("errorSourceTool", { name: item.toolName ?? "?" })
                              : t("errorSourceSystem")}
                          </Badge>
                          {item.code && (
                            <span className="text-muted-foreground truncate font-mono text-2xs">
                              {item.code}
                            </span>
                          )}
                        </div>
                        {item.timestamp !== undefined && (
                          <span className="text-muted-foreground text-2xs shrink-0">
                            {new Date(item.timestamp).toLocaleTimeString()}
                          </span>
                        )}
                      </div>
                      <p className="text-destructive p-3 text-sm break-words whitespace-pre-wrap">
                        {item.message}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section className="flex flex-col gap-2">
              {errorItems.length > 0 && (
                <h3 className="text-foreground text-sm font-medium">{t("debugSectionTitle")}</h3>
              )}
              {debugItems.length === 0 ? (
                showGlobalEmpty ? (
                  <div className="text-muted-foreground border-border/70 bg-muted/30 rounded-lg border p-4 text-sm">
                    {t("empty")}
                  </div>
                ) : (
                  <div className="text-muted-foreground border-border/70 bg-muted/30 rounded-lg border p-4 text-sm">
                    {includeDebug ? t("debugEmpty") : t("debugEmptyClosed")}
                  </div>
                )
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
            </section>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
