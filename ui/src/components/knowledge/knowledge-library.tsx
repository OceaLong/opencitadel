"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { AddDocumentDialog } from "@/components/knowledge/add-document-dialog";
import { CreateKBDialog } from "@/components/knowledge/create-kb-dialog";
import { formatIngestStreamError } from "@/components/knowledge/knowledge-utils";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

import { useAuth } from "@/providers/auth-provider";
import { knowledgeApi } from "@/lib/api/knowledge";
import { sessionApi } from "@/lib/api/session";
import type { KnowledgeBase, SessionMode } from "@/lib/api/types";
import { IconAdd, IconKnowledge, IconLoading, IconRefresh } from "@/lib/icons";
import { cn } from "@/lib/utils";

const TERMINAL_KB_STATUSES = new Set<KnowledgeBase["status"]>(["ready", "failed"]);

function isKbIngesting(kb: KnowledgeBase, ingestingIds: Set<string>): boolean {
  return (
    ingestingIds.has(kb.id) ||
    (!TERMINAL_KB_STATUSES.has(kb.status) && Boolean(kb.ingest_task_id))
  );
}

export function KnowledgeLibrary() {
  const router = useRouter();
  const t = useTranslations("knowledge");
  const { user } = useAuth();
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [addOpenFor, setAddOpenFor] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [ingestingIds, setIngestingIds] = useState<Set<string>>(new Set());
  const ingestCleanupRef = useRef<Map<string, () => void>>(new Map());

  const loadList = useCallback(async () => {
    if (!user) {
      setItems([]);
      return;
    }
    try {
      const data = await knowledgeApi.list();
      setItems(data.knowledge_bases);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("loadListFailed"));
    }
  }, [user, t]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    return () => {
      ingestCleanupRef.current.forEach((cleanup) => cleanup());
      ingestCleanupRef.current.clear();
    };
  }, []);

  const watchIngest = useCallback(
    (id: string, ingestTaskId?: string | null) => {
      if (!ingestTaskId || ingestCleanupRef.current.has(id)) return;
      setIngestingIds((prev) => new Set(prev).add(id));
      const finish = () => {
        setIngestingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        ingestCleanupRef.current.delete(id);
        void loadList();
      };
      const cleanup = knowledgeApi.ingestStream(
        id,
        (ev) => {
          if (ev.type === "error") {
            toast.error(formatIngestStreamError(ev));
            finish();
          } else if (ev.type === "done") {
            finish();
          }
        },
        () => {
          toast.error(t("ingestStreamFailed"));
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
    [loadList, t],
  );

  useEffect(() => {
    for (const kb of items) {
      if (kb.ingest_task_id && !TERMINAL_KB_STATUSES.has(kb.status)) {
        watchIngest(kb.id, kb.ingest_task_id);
      }
    }
  }, [items, watchIngest]);

  useEffect(() => {
    const hasIngesting = items.some((kb) => isKbIngesting(kb, ingestingIds));
    if (!hasIngesting) return;
    const timer = setInterval(() => {
      void loadList();
    }, 5000);
    return () => clearInterval(timer);
  }, [items, ingestingIds, loadList]);

  const startTask = async (kbId: string, mode: SessionMode = "ask") => {
    setStartingId(kbId);
    try {
      const data = await sessionApi.createSession({ knowledge_base_id: kbId, mode });
      router.push(`/sessions/${data.session_id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("startTaskFailed"));
    } finally {
      setStartingId(null);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        size="sm"
        className="px-4 py-3"
        title={
          <span className="inline-flex items-center gap-2">
            <IconKnowledge className="size-5" />
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
          {items.map((kb) => {
            const ingesting = isKbIngesting(kb, ingestingIds);
            return (
              <Card key={kb.id} className={cn(ingesting && "border-primary/30")}>
                <CardHeader className="pb-2">
                  <CardTitle className="truncate text-base">{kb.name}</CardTitle>
                  <CardDescription className="text-xs">
                    {t("statusDocCount", { status: kb.status, count: kb.doc_count ?? 0 })}
                    {ingesting && (
                      <span className="ml-2 inline-flex items-center gap-1">
                        <IconLoading className="size-3 animate-spin" />
                        {t("indexingShort")}
                      </span>
                    )}
                    {kb.status === "failed" && kb.error && (
                      <span className="mt-1 block text-destructive">
                        {t("indexFailedDetail", { error: kb.error })}
                      </span>
                    )}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    disabled={startingId === kb.id || kb.status !== "ready"}
                    onClick={() => void startTask(kb.id, "ask")}
                  >
                    {startingId === kb.id ? <IconLoading className="size-4 animate-spin" /> : t("startAsk")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={startingId === kb.id || kb.status !== "ready"}
                    onClick={() => void startTask(kb.id, "agent")}
                  >
                    {t("startAgent")}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setAddOpenFor(kb.id)}>
                    {t("addDocument")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      try {
                        const updated = await knowledgeApi.reindex(kb.id);
                        watchIngest(kb.id, updated.ingest_task_id);
                        toast.success(t("reindexStarted"));
                      } catch (err) {
                        toast.error(err instanceof Error ? err.message : t("reindexFailed"));
                      }
                    }}
                  >
                    <IconRefresh className="mr-1 size-3" />
                    {t("reindex")}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
          {!items.length && <EmptyState title={t("empty")} className="col-span-full" />}
        </div>
      </ScrollArea>

      <CreateKBDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(kb) => {
          setItems((prev) => [kb, ...prev]);
        }}
      />
      {addOpenFor && (
        <AddDocumentDialog
          kbId={addOpenFor}
          open={Boolean(addOpenFor)}
          onOpenChange={(open) => !open && setAddOpenFor(null)}
          onAdded={(kb) => {
            watchIngest(kb.id, kb.ingest_task_id);
            void loadList();
          }}
        />
      )}
    </div>
  );
}
