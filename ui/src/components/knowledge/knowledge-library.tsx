"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { AddDocumentDialog } from "@/components/knowledge/add-document-dialog";
import { CreateKBDialog } from "@/components/knowledge/create-kb-dialog";
import {
  appendDocumentsPage,
  formatIngestStreamError,
} from "@/components/knowledge/knowledge-utils";
import { PageHeader } from "@/components/page-header";
import type { RecycleBinItem } from "@/components/recycle-bin-dialog";
import { RecycleBinDialog } from "@/components/recycle-bin-dialog";
import { ResourceVersionStatus } from "@/components/resource/resource-version-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";

import { type ResourceLibraryApi, useResourceLibrary } from "@/hooks/use-resource-library";
import { knowledgeApi } from "@/lib/api/knowledge";
import { sessionApi } from "@/lib/api/session";
import type { KnowledgeBase, KnowledgeDocument, SessionMode } from "@/lib/api/types";
import { IconAdd, IconDelete, IconKnowledge, IconLoading, IconMore } from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

// Statuses with a build actually in flight server-side. `pending` is NOT one:
// a freshly created KB sits in `pending` until the user uploads documents, and
// watching its ingest stream (which closes immediately — there is no build)
// just churns reconnects and polling forever.
const ACTIVE_BUILD_KB_STATUSES = new Set<KnowledgeBase["status"]>([
  "parsing",
  "chunking",
  "indexing",
  "graph_building",
]);

const PAGE_SIZE = 50;
const MAX_PAGE_SIZE = 200;

const KB_STATUS_LABEL_KEYS: Record<KnowledgeBase["status"], `status.${KnowledgeBase["status"]}`> = {
  pending: "status.pending",
  parsing: "status.parsing",
  chunking: "status.chunking",
  indexing: "status.indexing",
  graph_building: "status.graph_building",
  ready: "status.ready",
  failed: "status.failed",
};

async function startKnowledgeTask(
  kbId: string,
  versionId: string,
  mode: SessionMode,
  createSession: typeof sessionApi.createSession,
  push: (href: string) => void,
): Promise<void> {
  const data = await createSession({
    knowledge_base_id: kbId,
    knowledge_base_version_id: versionId,
    mode,
  });
  push(`/sessions/${data.session_id}`);
}

type DocsPage = { items: KnowledgeDocument[]; total: number; loading: boolean };

type PendingDelete =
  | { kind: "kb"; kb: KnowledgeBase }
  | { kind: "document"; kbId: string; doc: KnowledgeDocument }
  | null;

function isKbIngesting(kb: KnowledgeBase, ingestingIds: Set<string>): boolean {
  return ingestingIds.has(kb.id) || ACTIVE_BUILD_KB_STATUSES.has(kb.status);
}

// Stable module-level adapter: `knowledgeApi` is itself a stable singleton, so
// this object never needs to be recreated per render (see
// `use-resource-library.ts` for why identity stability matters here).
const knowledgeLibraryApi: ResourceLibraryApi<KnowledgeBase, never> = {
  list: async () => (await knowledgeApi.list()).knowledge_bases,
  remove: (id) => knowledgeApi.delete(id),
  ingestStream: knowledgeApi.ingestStream,
};

function knowledgeShouldPoll(
  kb: KnowledgeBase,
  { ingestingIds }: { ingestingIds: Set<string> },
): boolean {
  return isKbIngesting(kb, ingestingIds);
}

export function KnowledgeLibrary() {
  const router = useRouter();
  const t = useTranslations("knowledge");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const [docsByKb, setDocsByKb] = useState<Record<string, DocsPage>>({});
  const [expandedKbs, setExpandedKbs] = useState<Set<string>>(new Set());
  const [createOpen, setCreateOpen] = useState(false);
  const [addOpenFor, setAddOpenFor] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);
  const [recycleOpen, setRecycleOpen] = useState(false);
  // Mirrors of state used inside refreshExpandedDocs so that callback can read
  // the latest value without depending on the state itself — otherwise it
  // would be re-created (and the hook's `onLoaded` along with it) on every
  // expand/collapse toggle, since expandedKbs/docsByKb get new references often.
  const expandedKbsRef = useRef<Set<string>>(new Set());
  const docsByKbRef = useRef<Record<string, DocsPage>>({});

  useEffect(() => {
    expandedKbsRef.current = expandedKbs;
  }, [expandedKbs]);

  useEffect(() => {
    docsByKbRef.current = docsByKb;
  }, [docsByKb]);

  const loadDocsPage = useCallback(
    async (kbId: string, offset: number) => {
      setDocsByKb((prev) => ({
        ...prev,
        [kbId]: {
          items: offset === 0 ? [] : (prev[kbId]?.items ?? []),
          total: prev[kbId]?.total ?? 0,
          loading: true,
        },
      }));
      try {
        const page = await knowledgeApi.listDocuments(kbId, PAGE_SIZE, offset);
        setDocsByKb((prev) => ({
          ...prev,
          [kbId]: {
            items:
              offset === 0
                ? page.documents
                : appendDocumentsPage(prev[kbId]?.items ?? [], page.documents),
            total: page.total,
            loading: false,
          },
        }));
      } catch {
        setDocsByKb((prev) => ({
          ...prev,
          [kbId]: { ...(prev[kbId] ?? { items: [], total: 0 }), loading: false },
        }));
        toast.error(t("loadListFailed"));
      }
    },
    [t],
  );

  // Re-fetches an already-expanded card's documents from offset 0, but requests
  // enough items to cover what the user had already paged in (capped at
  // MAX_PAGE_SIZE), so auto-refreshes (SSE done / 5s poll / post-delete) don't
  // silently truncate a card back to the first page.
  const refreshExpandedDocs = useCallback(
    async (kbId: string) => {
      const currentCount = docsByKbRef.current[kbId]?.items.length ?? 0;
      const limit = Math.min(MAX_PAGE_SIZE, Math.max(PAGE_SIZE, currentCount));
      setDocsByKb((prev) => ({
        ...prev,
        [kbId]: {
          items: prev[kbId]?.items ?? [],
          total: prev[kbId]?.total ?? 0,
          loading: true,
        },
      }));
      try {
        const page = await knowledgeApi.listDocuments(kbId, limit, 0);
        setDocsByKb((prev) => ({
          ...prev,
          [kbId]: { items: page.documents, total: page.total, loading: false },
        }));
      } catch {
        setDocsByKb((prev) => ({
          ...prev,
          [kbId]: { ...(prev[kbId] ?? { items: [], total: 0 }), loading: false },
        }));
        toast.error(t("loadListFailed"));
      }
    },
    [t],
  );

  const {
    items,
    setItems,
    ingestingIds,
    startingId,
    load: loadList,
    remove: removeKnowledgeBase,
    watchIngest,
    startTask: runStartTask,
  } = useResourceLibrary<KnowledgeBase, never>({
    api: knowledgeLibraryApi,
    enabled: Boolean(user),
    onReset: () => setDocsByKb({}),
    onLoaded: () => {
      for (const kbId of expandedKbsRef.current) {
        void refreshExpandedDocs(kbId);
      }
    },
    loadErrorMessage: t("loadListFailed"),
    shouldPoll: knowledgeShouldPoll,
    pollMs: 5000,
    formatIngestError: formatIngestStreamError,
    onStreamConnectError: () => toast.error(t("ingestStreamFailed")),
  });

  useEffect(() => {
    for (const kb of items) {
      if (ACTIVE_BUILD_KB_STATUSES.has(kb.status)) {
        watchIngest(kb.id);
      }
    }
  }, [items, watchIngest]);

  const startTask = async (kbId: string, versionId: string, mode: SessionMode = "ask") => {
    await runStartTask(
      kbId,
      () => startKnowledgeTask(kbId, versionId, mode, sessionApi.createSession, router.push),
      t("startTaskFailed"),
    );
  };

  const handleDeleteConfirm = async () => {
    if (!pendingDelete) return;
    try {
      if (pendingDelete.kind === "kb") {
        await removeKnowledgeBase(pendingDelete.kb.id);
        setDocsByKb((prev) => {
          const next = { ...prev };
          delete next[pendingDelete.kb.id];
          return next;
        });
        setExpandedKbs((prev) => {
          if (!prev.has(pendingDelete.kb.id)) return prev;
          const next = new Set(prev);
          next.delete(pendingDelete.kb.id);
          return next;
        });
        toast.success(t("deleteKbSuccess", { name: pendingDelete.kb.name }));
      } else {
        const updated = await knowledgeApi.deleteDocument(pendingDelete.kbId, pendingDelete.doc.id);
        setItems((prev) => prev.map((kb) => (kb.id === updated.id ? updated : kb)));
        setDocsByKb((prev) => {
          const existing = prev[pendingDelete.kbId];
          if (!existing) return prev;
          return {
            ...prev,
            [pendingDelete.kbId]: {
              ...existing,
              items: existing.items.filter((doc) => doc.id !== pendingDelete.doc.id),
              total: Math.max(0, existing.total - 1),
            },
          };
        });
        toast.success(t("deleteDocumentSuccess", { title: pendingDelete.doc.title }));
      }
      setPendingDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("deleteFailed"));
    }
  };

  const loadDeleted = useCallback(async (): Promise<RecycleBinItem[]> => {
    const data = await knowledgeApi.listDeleted();
    return data.knowledge_bases.map((kb) => ({
      id: kb.id,
      primary: kb.name,
      secondary: t("statusDocCount", {
        status: t(KB_STATUS_LABEL_KEYS[kb.status]),
        count: kb.doc_count ?? 0,
      }),
    }));
  }, [t]);

  const deleteDialogTitle =
    pendingDelete?.kind === "document" ? t("deleteDocumentTitle") : t("deleteKbTitle");
  const deleteDialogDescription =
    pendingDelete?.kind === "document" ? t("deleteDocumentDescription") : t("deleteKbDescription");

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        className="border-border/70 border-b px-4 py-3 sm:px-6"
        title={
          <span className="inline-flex items-center gap-2">
            <IconKnowledge className="size-5" />
            {t("title")}
          </span>
        }
        description={t("librarySubtitle")}
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRecycleOpen(true)}
              aria-label={t("recycleBinTitle")}
            >
              <IconDelete className="mr-1 size-4" />
              {t("recycleBin")}
            </Button>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <IconAdd className="mr-1 size-4" />
              {t("create")}
            </Button>
          </div>
        }
      />

      <ScrollArea className="flex-1">
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((kb) => {
            const ingesting = isKbIngesting(kb, ingestingIds);
            const docsPage = docsByKb[kb.id];
            const documents = docsPage?.items ?? [];
            const expanded = expandedKbs.has(kb.id);
            const statusLabel = t(KB_STATUS_LABEL_KEYS[kb.status]);
            return (
              <Card key={kb.id} className={cn(ingesting && "border-primary/30")}>
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="truncate text-base">{kb.name}</CardTitle>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button size="icon-sm" variant="ghost" className="shrink-0">
                          <IconMore className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => setAddOpenFor(kb.id)}>
                          {t("addDocument")}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          disabled={ingesting}
                          title={ingesting ? t("deleteBlockedIngesting") : undefined}
                          onSelect={() => setPendingDelete({ kind: "kb", kb })}
                        >
                          <IconDelete className="mr-1 size-3" />
                          {tCommon("delete")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  <CardDescription className="text-xs">
                    {t("statusDocCount", { status: statusLabel, count: kb.doc_count ?? 0 })}
                    {" · "}
                    {t("readyDocCount", {
                      ready: kb.ready_doc_count ?? 0,
                      count: kb.doc_count ?? 0,
                    })}
                    {ingesting && (
                      <span className="ml-2 inline-flex items-center gap-1">
                        <IconLoading className="size-3 animate-spin" />
                        {t("indexingShort")}
                      </span>
                    )}
                    {kb.status === "failed" && kb.error && (
                      <span className="text-destructive mt-1 block">
                        {t("indexFailedDetail", { error: kb.error })}
                      </span>
                    )}
                    {kb.status !== "failed" && kb.error && (
                      <span className="text-warning mt-1 block">
                        {t("partialFailureWarning", { error: kb.error })}
                      </span>
                    )}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <ResourceVersionStatus
                    api={knowledgeApi}
                    resourceId={kb.id}
                    onBuildChanged={loadList}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      disabled={startingId === kb.id || !kb.active_version_id}
                      onClick={() =>
                        kb.active_version_id
                          ? void startTask(kb.id, kb.active_version_id, "ask")
                          : undefined
                      }
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
                      disabled={startingId === kb.id || !kb.active_version_id}
                      onClick={() =>
                        kb.active_version_id
                          ? void startTask(kb.id, kb.active_version_id, "agent")
                          : undefined
                      }
                    >
                      {t("startAgent")}
                    </Button>
                  </div>

                  <div className="space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-muted-foreground text-xs font-medium">
                        {t("documentsLabel")}
                      </p>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setExpandedKbs((prev) => {
                            const next = new Set(prev);
                            if (next.has(kb.id)) {
                              next.delete(kb.id);
                            } else {
                              next.add(kb.id);
                              if (!docsByKb[kb.id]) void loadDocsPage(kb.id, 0);
                            }
                            return next;
                          });
                        }}
                      >
                        {t("showDocuments")}
                      </Button>
                    </div>
                    {expanded && (
                      <div className="space-y-1">
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
                                  className="text-destructive hover:text-destructive size-7 shrink-0"
                                  disabled={ingesting}
                                  title={
                                    ingesting ? t("deleteBlockedIngesting") : tCommon("delete")
                                  }
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
                        {(docsPage?.items.length ?? 0) < (docsPage?.total ?? 0) && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={docsPage?.loading}
                            onClick={() => void loadDocsPage(kb.id, docsPage?.items.length ?? 0)}
                          >
                            {t("loadMoreDocuments", {
                              shown: docsPage?.items.length ?? 0,
                              total: docsPage?.total ?? 0,
                            })}
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
          {!items.length && (
            <EmptyState variant="dashed" title={t("empty")} className="col-span-full" />
          )}
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
            watchIngest(kb.id);
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

      <RecycleBinDialog
        open={recycleOpen}
        onOpenChange={setRecycleOpen}
        title={t("recycleBinTitle")}
        description={t("recycleBinDescription")}
        load={loadDeleted}
        restore={knowledgeApi.restore}
        purge={knowledgeApi.purge}
        onChanged={loadList}
      />
    </div>
  );
}
