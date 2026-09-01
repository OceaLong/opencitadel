"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { AlertTriangle } from "lucide-react";

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

import { modelErrorMessage } from "@/lib/api/inference-errors";
import type { SSEEventData } from "@/lib/api/types";
import { toMillis } from "@/lib/session-events";
import { toBcp47 } from "@/lib/utils";

type ExecutionIssue = {
  id: string;
  source: "run" | "tool";
  label: string;
  code?: string | null;
  timestamp?: number;
};

function executionIssues(events: SSEEventData[]): ExecutionIssue[] {
  const issues: ExecutionIssue[] = [];
  for (const event of events) {
    if (event.type === "error") {
      issues.push({
        id: event.data.event_id || `run-${issues.length}`,
        source: "run",
        label: modelErrorMessage(event.data.code) || event.data.error,
        code: event.data.code,
        timestamp: toMillis(event.data.created_at),
      });
    } else if (event.type === "tool" && event.data.status === "failed") {
      issues.push({
        id: event.data.event_id || event.data.tool_call_id || `tool-${issues.length}`,
        source: "tool",
        label: event.data.name,
        timestamp: toMillis(event.data.created_at),
      });
    }
  }
  return issues;
}

export function SessionErrorSheet({
  events,
  compact,
}: {
  events: SSEEventData[];
  compact?: boolean;
}) {
  const t = useTranslations("sessionErrors");
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const issues = useMemo(() => executionIssues(events), [events]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size={compact ? "icon-sm" : "sm"}
          className="relative"
          title={t("title")}
        >
          <AlertTriangle className="size-4" />
          {!compact ? <span className="ml-1">{t("button")}</span> : null}
          {issues.length > 0 ? (
            <span className="bg-destructive text-primary-foreground absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 font-mono text-[10px] font-medium">
              {issues.length > 99 ? "99+" : issues.length}
            </span>
          ) : null}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <ScrollArea className="mt-4 h-[calc(100vh-8rem)] pr-3">
          {issues.length === 0 ? (
            <div className="text-muted-foreground border-border/70 bg-muted/30 rounded-lg border p-4 text-sm">
              {t("empty")}
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {issues.map((issue) => (
                <div
                  key={issue.id}
                  className="border-destructive/30 bg-destructive/5 rounded-lg border p-3"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <Badge variant="outline" className="border-destructive/40 text-destructive">
                      {issue.source === "tool" ? t("tool") : t("run")}
                    </Badge>
                    {issue.timestamp ? (
                      <span className="text-muted-foreground text-xs">
                        {new Date(issue.timestamp).toLocaleTimeString(toBcp47(locale))}
                      </span>
                    ) : null}
                  </div>
                  <p className="text-destructive text-sm break-words">{issue.label}</p>
                  {issue.code ? (
                    <code className="text-muted-foreground mt-1 block text-xs">{issue.code}</code>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
