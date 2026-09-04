"use client";

import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import type { RecycleBinItem } from "@/components/recycle-bin-dialog";
import { RecycleBinDialog } from "@/components/recycle-bin-dialog";
import { DeleteSessionDialog } from "@/components/session/delete-session-dialog";
import { SessionItem } from "@/components/session/session-item";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ItemGroup } from "@/components/ui/item";

import { useSessions } from "@/hooks/use-sessions";
import type { Session } from "@/lib/api";
import { sessionApi } from "@/lib/api";
import { getSessionContextKind, IconSearch, type SessionContextKind } from "@/lib/icons";
import { cn } from "@/lib/utils";

type ContextFilter = "all" | SessionContextKind;

const FILTER_OPTIONS: ContextFilter[] = ["all", "general", "knowledge"];

/** 搜索输入 debounce 时长（毫秒） */
const SEARCH_DEBOUNCE_MS = 300;

export function SessionList() {
  const router = useRouter();
  const params = useParams();
  const t = useTranslations("sessionList");
  const tCommon = useTranslations("common");
  const { sessions, loading, error, refresh, deleteSession, query, setQuery } = useSessions();
  const [filter, setFilter] = useState<ContextFilter>("all");
  const [pendingDeleteSession, setPendingDeleteSession] = useState<Session | null>(null);
  const [recycleOpen, setRecycleOpen] = useState(false);
  // 本地即时输入值；以 query 为初始值，经 debounce 后回写到 provider。
  const [searchInput, setSearchInput] = useState(query);

  // 输入 debounce：仅在停止输入 300ms 后把关键词经 `q` 传给列表/流接口。
  useEffect(() => {
    if (searchInput === query) return;
    const timer = setTimeout(() => setQuery(searchInput), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, query, setQuery]);

  const filteredSessions = useMemo(() => {
    if (filter === "all") return sessions;
    return sessions.filter((session) => getSessionContextKind(session) === filter);
  }, [filter, sessions]);

  const handleSessionClick = useCallback(
    (sessionId: string) => {
      router.push(`/sessions/${sessionId}`);
    },
    [router],
  );

  const handleDeleteRequest = useCallback((session: Session) => {
    setPendingDeleteSession(session);
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    if (!pendingDeleteSession) return;

    const sessionTitle = pendingDeleteSession.title || tCommon("newTask");
    const result = await deleteSession(pendingDeleteSession.session_id);

    if (result.success) {
      toast.success(t("deleteSuccess", { title: sessionTitle }));
      if (params?.id === pendingDeleteSession.session_id) {
        router.push("/");
      }
    } else {
      // 服务端给了具体原因（如活动 Run 未终态）就原样展示，比笼统的
      // "请重试" 可诊断得多。
      toast.error(result.message || t("deleteFailed", { title: sessionTitle }));
    }

    setPendingDeleteSession(null);
  }, [pendingDeleteSession, deleteSession, params?.id, router, t, tCommon]);

  const handleDialogOpenChange = useCallback((open: boolean) => {
    if (!open) {
      setPendingDeleteSession(null);
    }
  }, []);

  const loadDeleted = useCallback(async (): Promise<RecycleBinItem[]> => {
    const data = await sessionApi.getDeletedSessions();
    return data.sessions.map((session) => ({
      id: session.session_id,
      primary: session.title || tCommon("newTask"),
      secondary: session.latest_message || undefined,
    }));
  }, [tCommon]);

  let listBody: ReactNode;
  if (loading) {
    listBody = (
      <ItemGroup className="gap-1">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="flex animate-pulse items-center gap-2 p-2">
            <div className="bg-muted size-8 rounded-full" />
            <div className="flex-1 space-y-1.5">
              <div className="bg-muted h-3.5 w-3/4 rounded" />
              <div className="bg-muted h-3 w-1/2 rounded" />
            </div>
          </div>
        ))}
      </ItemGroup>
    );
  } else if (error) {
    listBody = (
      <div className="text-muted-foreground flex flex-col items-center gap-2 py-8 text-sm">
        <p>{t("loadError")}</p>
        <button
          className="text-primary cursor-pointer underline underline-offset-4"
          onClick={refresh}
        >
          {t("retry")}
        </button>
      </div>
    );
  } else if (filteredSessions.length === 0) {
    listBody = (
      <EmptyState
        title={
          query.trim() ? t("searchEmpty") : sessions.length === 0 ? t("empty") : t("filterEmpty")
        }
        className="py-8"
      />
    );
  } else {
    listBody = (
      <ItemGroup className="gap-1">
        {filteredSessions.map((session) => (
          <SessionItem
            key={session.session_id}
            session={session}
            isActive={session.session_id === String(params?.id ?? "")}
            onClick={handleSessionClick}
            onDelete={handleDeleteRequest}
          />
        ))}
      </ItemGroup>
    );
  }

  return (
    <>
      <div className="mb-2 flex items-center gap-1">
        <div className="relative flex-1">
          <IconSearch className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
          <Input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("searchPlaceholder")}
            aria-label={t("searchPlaceholder")}
            className="h-8 pl-7 text-sm"
          />
        </div>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          aria-label={t("recycleBinTitle")}
          title={t("recycleBinTitle")}
          onClick={() => setRecycleOpen(true)}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>

      <div className="mb-2 flex flex-wrap gap-1">
        {FILTER_OPTIONS.map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            variant={filter === option ? "secondary" : "ghost"}
            className={cn("text-2xs h-6 px-2", filter === option && "font-medium")}
            onClick={() => setFilter(option)}
          >
            {t(`filter.${option}`)}
          </Button>
        ))}
      </div>

      {listBody}

      <DeleteSessionDialog
        open={!!pendingDeleteSession}
        onOpenChange={handleDialogOpenChange}
        onConfirm={handleDeleteConfirm}
      />

      <RecycleBinDialog
        open={recycleOpen}
        onOpenChange={setRecycleOpen}
        title={t("recycleBinTitle")}
        description={t("recycleBinDescription")}
        load={loadDeleted}
        restore={sessionApi.restoreSession}
        purge={sessionApi.purgeSession}
        onChanged={refresh}
      />
    </>
  );
}
