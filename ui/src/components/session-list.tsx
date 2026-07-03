"use client";

import { useCallback, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { DeleteSessionDialog } from "@/components/delete-session-dialog";
import { SessionItem } from "@/components/session-item";
import { Button } from "@/components/ui/button";
import { ItemGroup } from "@/components/ui/item";

import { useSessions } from "@/hooks/use-sessions";
import type { Session } from "@/lib/api";
import { getSessionContextKind, type SessionContextKind } from "@/lib/icons";
import { cn } from "@/lib/utils";

type ContextFilter = "all" | SessionContextKind;

const FILTER_OPTIONS: ContextFilter[] = ["all", "general", "codebase", "knowledge", "hybrid"];

export function SessionList() {
  const router = useRouter();
  const params = useParams();
  const t = useTranslations("sessionList");
  const tCommon = useTranslations("common");
  const { sessions, loading, error, refresh, deleteSession } = useSessions();
  const [filter, setFilter] = useState<ContextFilter>("all");
  const [pendingDeleteSession, setPendingDeleteSession] = useState<Session | null>(null);

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
    const success = await deleteSession(pendingDeleteSession.session_id);

    if (success) {
      toast.success(t("deleteSuccess", { title: sessionTitle }));
      if (params?.id === pendingDeleteSession.session_id) {
        router.push("/");
      }
    } else {
      toast.error(t("deleteFailed", { title: sessionTitle }));
    }

    setPendingDeleteSession(null);
  }, [pendingDeleteSession, deleteSession, params?.id, router, t, tCommon]);

  const handleDialogOpenChange = useCallback((open: boolean) => {
    if (!open) {
      setPendingDeleteSession(null);
    }
  }, []);

  if (loading) {
    return (
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
  }

  if (error) {
    return (
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
  }

  return (
    <>
      <div className="mb-2 flex flex-wrap gap-1">
        {FILTER_OPTIONS.map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            variant={filter === option ? "secondary" : "ghost"}
            className={cn("h-6 px-2 text-2xs", filter === option && "font-medium")}
            onClick={() => setFilter(option)}
          >
            {t(`filter.${option}`)}
          </Button>
        ))}
      </div>

      {filteredSessions.length === 0 ? (
        <div className="text-muted-foreground py-8 text-center text-sm">
          {sessions.length === 0 ? t("empty") : t("filterEmpty")}
        </div>
      ) : (
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
      )}

      <DeleteSessionDialog
        open={!!pendingDeleteSession}
        onOpenChange={handleDialogOpenChange}
        onConfirm={handleDeleteConfirm}
      />
    </>
  );
}
