"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bot,
  Brain,
  Copy,
  HelpCircle,
  Loader2,
  Settings2,
  Trash2,
  UserRound,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { memoryApi } from "@/lib/api/memory";
import type { SessionMemoryData } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type Props = {
  sessionId: string;
  editable?: boolean;
  /** 自定义触发按钮，不传则使用默认样式 */
  trigger?: React.ReactNode;
  /** 紧凑图标按钮模式（用于会话头部） */
  compact?: boolean;
};

type MemoryRole = "system" | "user" | "assistant" | "tool" | "unknown";

type RoleStyle = {
  icon: LucideIcon;
  cardClass: string;
  headerClass: string;
  accentClass: string;
  badgeClass: string;
  contentClass: string;
};

const ROLE_STYLES: Record<MemoryRole, RoleStyle> = {
  system: {
    icon: Settings2,
    cardClass: "border-slate-300/70 dark:border-slate-600/50",
    headerClass: "bg-slate-100/80 dark:bg-slate-800/40",
    accentClass: "border-l-slate-400 dark:border-l-slate-500",
    badgeClass:
      "border-slate-300/60 bg-slate-100 text-slate-700 dark:border-slate-600 dark:bg-slate-800/60 dark:text-slate-300",
    contentClass: "bg-slate-50/50 dark:bg-slate-900/20",
  },
  user: {
    icon: UserRound,
    cardClass: "border-blue-200/70 dark:border-blue-700/40",
    headerClass: "bg-blue-50/80 dark:bg-blue-950/30",
    accentClass: "border-l-blue-400 dark:border-l-blue-500",
    badgeClass:
      "border-blue-200/60 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/50 dark:text-blue-300",
    contentClass: "bg-blue-50/30 dark:bg-blue-950/15",
  },
  assistant: {
    icon: Bot,
    cardClass: "border-violet-200/70 dark:border-violet-700/40",
    headerClass: "bg-violet-50/80 dark:bg-violet-950/30",
    accentClass: "border-l-violet-400 dark:border-l-violet-500",
    badgeClass:
      "border-violet-200/60 bg-violet-50 text-violet-700 dark:border-violet-800 dark:bg-violet-950/50 dark:text-violet-300",
    contentClass: "bg-violet-50/30 dark:bg-violet-950/15",
  },
  tool: {
    icon: Wrench,
    cardClass: "border-amber-200/70 dark:border-amber-700/40",
    headerClass: "bg-amber-50/80 dark:bg-amber-950/30",
    accentClass: "border-l-amber-400 dark:border-l-amber-500",
    badgeClass:
      "border-amber-200/60 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-300",
    contentClass: "bg-amber-50/30 dark:bg-amber-950/15",
  },
  unknown: {
    icon: HelpCircle,
    cardClass: "border-border/70",
    headerClass: "bg-muted/50",
    accentClass: "border-l-muted-foreground/40",
    badgeClass: "border-border text-muted-foreground bg-muted/40",
    contentClass: "bg-muted/20",
  },
};

function normalizeRole(role: unknown): MemoryRole {
  const raw = String(role ?? "unknown").toLowerCase();
  if (raw === "system" || raw === "user" || raw === "assistant" || raw === "tool") {
    return raw;
  }
  return "unknown";
}

function getRoleStyle(role: unknown): RoleStyle {
  return ROLE_STYLES[normalizeRole(role)];
}

function formatArrayContent(content: unknown[]): string {
  const parts = content
    .map((part) => {
      if (typeof part === "string") return part;
      if (typeof part !== "object" || part === null) return null;

      const item = part as Record<string, unknown>;
      if (typeof item.text === "string") return item.text;
      if (typeof item.content === "string") return item.content;
      if (item.type === "text" && typeof item.text === "string") return item.text;
      return null;
    })
    .filter((part): part is string => Boolean(part));

  if (parts.length > 0) {
    return parts.join("\n\n").trim();
  }

  return JSON.stringify(content, null, 2).trim();
}

function formatMessageContent(msg: Record<string, unknown>): string {
  const { content } = msg;

  if (typeof content === "string") {
    return content.trim();
  }

  if (Array.isArray(content)) {
    return formatArrayContent(content);
  }

  if (content != null && typeof content === "object") {
    return JSON.stringify(content, null, 2).trim();
  }

  if (content != null) {
    return String(content).trim();
  }

  return JSON.stringify(msg, null, 2).trim();
}

function getMessageMeta(msg: Record<string, unknown>): string | null {
  const fn = msg._function_name ?? msg.function_name;
  if (typeof fn === "string" && fn.trim()) {
    return fn.trim();
  }

  const toolCallId = msg.tool_call_id;
  if (typeof toolCallId === "string" && toolCallId.trim()) {
    return toolCallId.length > 16 ? `${toolCallId.slice(0, 16)}…` : toolCallId;
  }

  return null;
}

function MemoryMessageCard({
  index,
  msg,
  editable,
  onDelete,
}: {
  index: number;
  msg: Record<string, unknown>;
  editable: boolean;
  onDelete: () => void;
}) {
  const t = useTranslations("sessionMemory");
  const tCommon = useTranslations("common");
  const content = formatMessageContent(msg);
  const displayContent = content.slice(0, 2000) + (content.length > 2000 ? "\n…" : "");
  const role = normalizeRole(msg.role);
  const roleStyle = getRoleStyle(msg.role);
  const RoleIcon = roleStyle.icon;
  const meta = getMessageMeta(msg);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  return (
    <div
      className={cn(
        "group overflow-hidden rounded-2xl border border-l-4 bg-card/95 shadow-[var(--shadow-card)] transition-shadow hover:shadow-[var(--shadow-card-hover)]",
        roleStyle.cardClass,
        roleStyle.accentClass,
      )}
    >
      <div
        className={cn(
          "flex items-center justify-between gap-2 border-b px-3 py-2.5",
          roleStyle.headerClass,
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          <div
            className={cn(
              "flex size-6 shrink-0 items-center justify-center rounded-md border",
              roleStyle.badgeClass,
            )}
          >
            <RoleIcon className="size-3.5" />
          </div>
          <div className="flex min-w-0 flex-col gap-0.5">
            <div className="flex min-w-0 items-center gap-1.5">
              <Badge
                variant="outline"
                className={cn("px-1.5 py-0 text-[10px] font-medium", roleStyle.badgeClass)}
              >
                {t(`roles.${role}`)}
              </Badge>
              <Badge variant="outline" className="px-1.5 py-0 font-mono text-[10px] opacity-70">
                #{index + 1}
              </Badge>
            </div>
            {meta && (
              <span className="text-muted-foreground truncate font-mono text-[10px]">{meta}</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5 opacity-80 transition-opacity group-hover:opacity-100">
          <Button size="icon" variant="ghost" className="size-7" onClick={handleCopy} title={tCommon("copy")}>
            <Copy className="size-3.5" />
          </Button>
          {editable && (
            <Button
              size="icon"
              variant="ghost"
              className="text-destructive hover:text-destructive size-7"
              onClick={onDelete}
              title={t("deleteTitle")}
            >
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
      <pre
        className={cn(
          "text-foreground/90 max-h-52 overflow-auto p-3.5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap",
          roleStyle.contentClass,
        )}
      >
        {displayContent || t("emptyMessage")}
      </pre>
    </div>
  );
}

export function SessionMemorySheet({
  sessionId,
  editable = true,
  trigger,
  compact = false,
}: Props) {
  const t = useTranslations("sessionMemory");
  const tCommon = useTranslations("common");
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<SessionMemoryData | null>(null);
  const [loading, setLoading] = useState(false);
  const [mounted, setMounted] = useState(false);

  const plannerCount = data?.planner?.length ?? 0;
  const reactCount = data?.react?.length ?? 0;
  const messageCount = plannerCount + reactCount;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const mem = await memoryApi.getSessionMemory(sessionId);
      setData(mem);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    setMounted(true);
    load().catch(() => {});
  }, [load]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const handleCompact = async (agent: string) => {
    try {
      await memoryApi.compactSessionMemory(sessionId, agent);
      toast.success(t("compacted"));
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("operationFailed"));
    }
  };

  const handleClear = async (agent: string) => {
    try {
      await memoryApi.clearSessionMemory(sessionId, agent);
      toast.success(t("cleared"));
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("operationFailed"));
    }
  };

  const handleDeleteMsg = async (agent: string, index: number) => {
    try {
      await memoryApi.deleteSessionMemoryMessage(sessionId, agent, index);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("deleteFailed"));
    }
  };

  const renderAgent = (agent: string, messages: Array<Record<string, unknown>>) => (
    <div className="space-y-3 pr-2">
      {editable && (
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={() => handleCompact(agent)}
          >
            {t("compact")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={() => handleClear(agent)}
          >
            {t("clear")}
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
        <p className="text-muted-foreground py-6 text-center text-sm">{tCommon("noMessages")}</p>
      )}
    </div>
  );

  const defaultTrigger = compact ? (
    <Button
      variant="ghost"
      size="icon-sm"
      className="flex-shrink-0 cursor-pointer"
      title={t("memoryTitle")}
    >
      <Brain />
    </Button>
  ) : (
    <Button variant="ghost" size="sm" className="h-8 gap-1">
      <Brain className="size-4" />
      {t("memoryButton")}
    </Button>
  );

  if (!mounted) {
    return trigger ?? defaultTrigger;
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger ?? defaultTrigger}</SheetTrigger>
      <SheetContent className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            {t("sheetTitle")}
            {!loading && (
              <Badge variant="secondary" className="text-xs font-normal">
                {t("messageCount", { count: messageCount })}
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            {t("sheetDescription")}
            {!loading && messageCount > 0 && (
              <span className="mt-1 block text-xs">
                {t("agentCounts", { planner: plannerCount, react: reactCount })}
              </span>
            )}
          </SheetDescription>
        </SheetHeader>
        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="size-6 animate-spin" />
          </div>
        ) : data ? (
          <Tabs defaultValue="planner" className="mt-4">
            <TabsList>
              <TabsTrigger value="planner">
                Planner
                <span className={cn("text-muted-foreground ml-1 text-xs")}>({plannerCount})</span>
              </TabsTrigger>
              <TabsTrigger value="react">
                ReAct
                <span className={cn("text-muted-foreground ml-1 text-xs")}>({reactCount})</span>
              </TabsTrigger>
            </TabsList>
            <ScrollArea className="mt-4 h-[calc(100vh-180px)]">
              <TabsContent value="planner">{renderAgent("planner", data.planner)}</TabsContent>
              <TabsContent value="react">{renderAgent("react", data.react)}</TabsContent>
            </ScrollArea>
          </Tabs>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
