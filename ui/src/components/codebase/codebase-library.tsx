"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { CreateCodebaseDialog } from "@/components/codebase/create-codebase-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

import { useAuth } from "@/providers/auth-provider";
import { codebaseApi } from "@/lib/api/codebase";
import { sessionApi } from "@/lib/api/session";
import type { Codebase, SessionMode } from "@/lib/api/types";
import {
  IconAdd,
  IconCodebase,
  IconDownload,
  IconLoading,
  IconRefresh,
} from "@/lib/icons";
import { cn } from "@/lib/utils";

export function CodebaseLibrary() {
  const router = useRouter();
  const t = useTranslations("codebase");
  const { user } = useAuth();
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [ingestingIds, setIngestingIds] = useState<Set<string>>(new Set());
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
    return () => {
      ingestCleanupRef.current.forEach((cleanup) => cleanup());
      ingestCleanupRef.current.clear();
    };
  }, []);

  const watchIngest = useCallback(
    (id: string) => {
      if (ingestCleanupRef.current.has(id)) return;
      setIngestingIds((prev) => new Set(prev).add(id));
      const cleanup = codebaseApi.ingestStream(
        id,
        (ev) => {
          if (ev.type === "done" || ev.type === "error") {
            setIngestingIds((prev) => {
              const next = new Set(prev);
              next.delete(id);
              return next;
            });
            ingestCleanupRef.current.delete(id);
            void loadCodebases();
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
      );
      ingestCleanupRef.current.set(id, cleanup);
    },
    [loadCodebases],
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

  return (
    <div className="flex h-full flex-col">
      <div className="border-border flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <IconCodebase className="size-5" />
          <div>
            <h1 className="text-sm font-semibold">{t("title")}</h1>
            <p className="text-muted-foreground text-xs">{t("librarySubtitle")}</p>
          </div>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <IconAdd className="mr-1 size-4" />
          {t("create")}
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {codebases.map((cb) => {
            const ingesting = ingestingIds.has(cb.id) || (cb.status !== "ready" && cb.status !== "failed");
            return (
              <Card key={cb.id} className={cn(ingesting && "border-primary/30")}>
                <CardHeader className="pb-2">
                  <CardTitle className="truncate text-base">{cb.name}</CardTitle>
                  <CardDescription className="text-xs">
                    {t("statusFileCount", { status: cb.status, count: cb.file_count ?? 0 })}
                    {ingesting && (
                      <span className="ml-2 inline-flex items-center gap-1">
                        <IconLoading className="size-3 animate-spin" />
                        {t("indexingShort")}
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
                </CardContent>
              </Card>
            );
          })}
          {!codebases.length && (
            <div className="text-muted-foreground col-span-full py-16 text-center text-sm">
              {t("empty")}
            </div>
          )}
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
    </div>
  );
}
