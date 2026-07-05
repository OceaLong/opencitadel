"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { CreateCodebaseDialog } from "@/components/codebase/create-codebase-dialog";
import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

import { useAuth } from "@/providers/auth-provider";
import { codebaseApi } from "@/lib/api/codebase";
import { sessionApi } from "@/lib/api/session";
import type { Codebase, CodebaseStatus, SessionMode } from "@/lib/api/types";
import {
  IconAdd,
  IconCodebase,
  IconDelete,
  IconDownload,
  IconLoading,
  IconRefresh,
} from "@/lib/icons";
import { cn } from "@/lib/utils";

const TERMINAL_CODEBASE_STATUSES: CodebaseStatus[] = ["ready", "failed"];
const INGEST_POLL_INTERVAL_MS = 3000;

const CODEBASE_STATUS_LABEL_KEYS: Record<CodebaseStatus, `status.${CodebaseStatus}`> = {
  pending: "status.pending",
  materializing: "status.materializing",
  analyzing: "status.analyzing",
  indexing: "status.indexing",
  generating: "status.generating",
  ready: "status.ready",
  failed: "status.failed",
};

function isIngestingStatus(status: CodebaseStatus): boolean {
  return !TERMINAL_CODEBASE_STATUSES.includes(status);
}

function truncateError(error: string, maxLength = 120): string {
  const text = error.trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...`;
}

export function CodebaseLibrary() {
  const router = useRouter();
  const t = useTranslations("codebase");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [ingestingIds, setIngestingIds] = useState<Set<string>>(new Set());
  const [pendingDelete, setPendingDelete] = useState<Codebase | null>(null);
  const ingestCleanupRef = useRef<Map<string, () => void>>(new Map());

  const loadCodebases = useCallback(async () => {
    if (!user) {
      setCodebases([]);
      return;
    }
    try {
      const data = await codebaseApi.list();
      setCodebases(data.codebases);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("loadError"));
    }
  }, [t, user]);

  useEffect(() => {
    void loadCodebases();
  }, [loadCodebases]);

  useEffect(() => {
    const hasActiveIngest = codebases.some(
      (cb) => isIngestingStatus(cb.status) && Boolean(cb.ingest_task_id),
    );
    if (!hasActiveIngest) return;

    const timer = window.setInterval(() => {
      void loadCodebases();
    }, INGEST_POLL_INTERVAL_MS);

    return () => window.clearInterval(timer);
  }, [codebases, loadCodebases]);

  useEffect(() => {
    return () => {
      ingestCleanupRef.current.forEach((cleanup) => cleanup());
      ingestCleanupRef.current.clear();
    };
  }, []);

  const watchIngest = useCallback(
    (id: string) => {
      if (ingestCleanupRef.current.has(id)) return;
      setIngestingIds((prev) => new Set(prev).add(id));
      const finish = () => {
        setIngestingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        ingestCleanupRef.current.delete(id);
        void loadCodebases();
      };
      const cleanup = codebaseApi.ingestStream(
        id,
        (ev) => {
          if (ev.type === "error") {
            const message =
              typeof ev.data?.error === "string" && ev.data.error.trim()
                ? ev.data.error
                : t("indexFailed");
            toast.error(t("indexFailedDetail", { error: message }));
            finish();
            return;
          }
          if (ev.type === "done") {
            finish();
          }
        },
        () => {
          setIngestingIds((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
          ingestCleanupRef.current.delete(id);
        },
        undefined,
        finish,
      );
      ingestCleanupRef.current.set(id, cleanup);
    },
    [loadCodebases, t],
  );

  const startTask = async (codebaseId: string, mode: SessionMode = "ask") => {
    setStartingId(codebaseId);
    try {
      const data = await sessionApi.createSession({ codebase_id: codebaseId, mode });
      router.push(`/sessions/${data.session_id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("startTaskFailed"));
    } finally {
      setStartingId(null);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!pendingDelete) return;
    const name = pendingDelete.name;
    try {
      await codebaseApi.delete(pendingDelete.id);
      setCodebases((prev) => prev.filter((cb) => cb.id !== pendingDelete.id));
      toast.success(t("deleteSuccess", { name }));
      setPendingDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("deleteFailed"));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        size="sm"
        className="px-4 py-3"
        title={
          <span className="inline-flex items-center gap-2">
            <IconCodebase className="size-5" />
            {t("title")}
          </span>
        }
        description={t("librarySubtitle")}
        actions={
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <IconAdd className="mr-1 size-4" />
            {t("create")}
          </Button>
        }
      />

      <ScrollArea className="flex-1">
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {codebases.map((cb) => {
            const ingesting =
              ingestingIds.has(cb.id) ||
              (isIngestingStatus(cb.status) && Boolean(cb.ingest_task_id));
            const statusLabel = t(CODEBASE_STATUS_LABEL_KEYS[cb.status]);
            return (
              <Card key={cb.id} className={cn(ingesting && "border-primary/30")}>
                <CardHeader className="pb-2">
                  <CardTitle className="truncate text-base">{cb.name}</CardTitle>
                  <CardDescription className="text-xs">
                    {t("statusFileCount", { status: statusLabel, count: cb.file_count ?? 0 })}
                    {ingesting && (
                      <span className="ml-2 inline-flex items-center gap-1">
                        <IconLoading className="size-3 animate-spin" />
                        {t("indexingShort")}
                      </span>
                    )}
                    {cb.status === "failed" && cb.error && (
                      <span
                        className="mt-1 block text-destructive"
                        title={cb.error}
                      >
                        {truncateError(cb.error)}
                      </span>
                    )}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    disabled={startingId === cb.id || cb.status !== "ready"}
                    onClick={() => void startTask(cb.id, "ask")}
                  >
                    {startingId === cb.id ? <IconLoading className="size-4 animate-spin" /> : t("startAsk")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={startingId === cb.id || cb.status !== "ready"}
                    onClick={() => void startTask(cb.id, "agent")}
                  >
                    {t("startAgent")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      await codebaseApi.reanalyze(cb.id);
                      watchIngest(cb.id);
                    }}
                  >
                    <IconRefresh className="mr-1 size-3" />
                    {t("reanalyze")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      try {
                        const data = await codebaseApi.download(cb.id);
                        toast.success(t("downloadSuccess", { key: data.snapshot_key }));
                      } catch (err) {
                        toast.error(err instanceof Error ? err.message : t("downloadFailed"));
                      }
                    }}
                  >
                    <IconDownload className="mr-1 size-3" />
                    {t("download")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive hover:text-destructive"
                    disabled={ingesting}
                    title={ingesting ? t("deleteBlockedIngesting") : undefined}
                    onClick={() => setPendingDelete(cb)}
                  >
                    <IconDelete className="mr-1 size-3" />
                    {tCommon("delete")}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
          {!codebases.length && <EmptyState title={t("empty")} className="col-span-full" />}
        </div>
      </ScrollArea>

      <CreateCodebaseDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(cb) => {
          setCodebases((prev) => [cb, ...prev]);
          watchIngest(cb.id);
        }}
      />

      <ConfirmDeleteDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title={t("deleteTitle")}
        description={t("deleteDescription")}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  );
}
