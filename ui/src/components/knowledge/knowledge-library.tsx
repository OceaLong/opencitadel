"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { AddDocumentDialog } from "@/components/knowledge/add-document-dialog";
import { CreateKBDialog } from "@/components/knowledge/create-kb-dialog";
import { formatIngestStreamError } from "@/components/knowledge/knowledge-utils";
import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

import { useAuth } from "@/providers/auth-provider";
import { knowledgeApi } from "@/lib/api/knowledge";
import { sessionApi } from "@/lib/api/session";
import type { KnowledgeBase, KnowledgeDocument, SessionMode } from "@/lib/api/types";
import { IconAdd, IconDelete, IconKnowledge, IconLoading, IconRefresh } from "@/lib/icons";
import { cn } from "@/lib/utils";

const TERMINAL_KB_STATUSES = new Set<KnowledgeBase["status"]>(["ready", "failed"]);

type PendingDelete =
  | { kind: "kb"; kb: KnowledgeBase }
  | { kind: "document"; kbId: string; doc: KnowledgeDocument }
  | null;

function isKbIngesting(kb: KnowledgeBase, ingestingIds: Set<string>): boolean {
  return (
    ingestingIds.has(kb.id) ||
    (!TERMINAL_KB_STATUSES.has(kb.status) && Boolean(kb.ingest_task_id))
  );
}

export function KnowledgeLibrary() {
  const router = useRouter();
  const t = useTranslations("knowledge");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [documentsByKb, setDocumentsByKb] = useState<Record<string, KnowledgeDocument[]>>({});
  const [createOpen, setCreateOpen] = useState(false);
  const [addOpenFor, setAddOpenFor] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [ingestingIds, setIngestingIds] = useState<Set<string>>(new Set());
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);
  const ingestCleanupRef = useRef<Map<string, () => void>>(new Map());

  const loadList = useCallback(async () => {
    if (!user) {
      setItems([]);
      setDocumentsByKb({});
      return;
    }
    try {
      const data = await knowledgeApi.list();
      setItems(data.knowledge_bases);
      const entries = await Promise.all(
        data.knowledge_bases.map(async (kb) => {
          try {
            const docs = await knowledgeApi.listDocuments(kb.id);
            return [kb.id, docs.documents] as const;
          } catch {
            return [kb.id, []] as const;
          }
        }),
      );
      setDocumentsByKb(Object.fromEntries(entries));
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

  const handleDeleteConfirm = async () => {
    if (!pendingDelete) return;
    try {
      if (pendingDelete.kind === "kb") {
        await knowledgeApi.delete(pendingDelete.kb.id);
        setItems((prev) => prev.filter((kb) => kb.id !== pendingDelete.kb.id));
        setDocumentsByKb((prev) => {
          const next = { ...prev };
          delete next[pendingDelete.kb.id];
          return next;
        });
        toast.success(t("deleteKbSuccess", { name: pendingDelete.kb.name }));
      } else {
        const updated = await knowledgeApi.deleteDocument(pendingDelete.kbId, pendingDelete.doc.id);
        setItems((prev) => prev.map((kb) => (kb.id === updated.id ? updated : kb)));
        setDocumentsByKb((prev) => ({
          ...prev,
          [pendingDelete.kbId]: (prev[pendingDelete.kbId] ?? []).filter(
            (doc) => doc.id !== pendingDelete.doc.id,
          ),
        }));
        if (updated.ingest_task_id) {
          watchIngest(updated.id, updated.ingest_task_id);
        }
        toast.success(t("deleteDocumentSuccess", { title: pendingDelete.doc.title }));
      }
      setPendingDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("deleteFailed"));
    }
  };

  const deleteDialogTitle =
    pendingDelete?.kind === "document" ? t("deleteDocumentTitle") : t("deleteKbTitle");
  const deleteDialogDescription =
    pendingDelete?.kind === "document"
      ? t("deleteDocumentDescription")
      : t("deleteKbDescription");

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
            const documents = documentsByKb[kb.id] ?? [];
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
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      disabled={startingId === kb.id || kb.status !== "ready"}
                      onClick={() => void startTask(kb.id, "ask")}
                    >
                      {startingId === kb.id ? (
                        <IconLoading className="size-4 animate-spin" />
                      ) : (
                        t("startAsk")
                      )}
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
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive hover:text-destructive"
                      disabled={ingesting}
                      title={ingesting ? t("deleteBlockedIngesting") : undefined}
                      onClick={() => setPendingDelete({ kind: "kb", kb })}
                    >
                      <IconDelete className="mr-1 size-3" />
                      {tCommon("delete")}
                    </Button>
                  </div>

                  <div className="space-y-1">
                    <p className="text-muted-foreground text-xs font-medium">{t("documentsLabel")}</p>
                    {documents.length === 0 ? (
                      <p className="text-muted-foreground text-xs">{t("noDocuments")}</p>
                    ) : (
                      <ul className="space-y-1">
                        {documents.map((doc) => (
                          <li
                            key={doc.id}
                            className="flex items-center justify-between gap-2 rounded-md border px-2 py-1"
                          >
                            <span className="truncate text-xs" title={doc.title}>
                              {doc.title}
                            </span>
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="size-7 shrink-0 text-destructive hover:text-destructive"
                              disabled={ingesting}
                              title={ingesting ? t("deleteBlockedIngesting") : tCommon("delete")}
                              onClick={() =>
                                setPendingDelete({ kind: "document", kbId: kb.id, doc })
                              }
                            >
                              <IconDelete className="size-3.5" />
                            </Button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
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

      <ConfirmDeleteDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title={deleteDialogTitle}
        description={deleteDialogDescription}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  );
}
