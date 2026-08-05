"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { CreateCodebaseDialog } from "@/components/codebase/create-codebase-dialog";
import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { ResourceVersionStatus } from "@/components/resource/resource-version-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

import { type ResourceLibraryApi, useResourceLibrary } from "@/hooks/use-resource-library";
import { codebaseApi } from "@/lib/api/codebase";
import { sessionApi } from "@/lib/api/session";
import type { Codebase, CodebaseStatus, CodebaseVersionsData, SessionMode } from "@/lib/api/types";
import {
  IconAdd,
  IconCodebase,
  IconDelete,
  IconDownload,
  IconLoading,
  IconRefresh,
} from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

const TERMINAL_CODEBASE_STATUSES: CodebaseStatus[] = ["ready", "failed"];
const ACTIVE_BUILD_STATES = new Set(["queued", "running"]);
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

function hasActiveBuild(history?: CodebaseVersionsData | null): boolean {
  const state = history?.active_build?.state;
  return Boolean(state && ACTIVE_BUILD_STATES.has(state));
}

function truncateError(error: string, maxLength = 120): string {
  const text = error.trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...`;
}

// Stable module-level adapter: `codebaseApi` is itself a stable singleton, so
// this object never needs to be recreated per render (see
// `use-resource-library.ts` for why identity stability matters here).
const codebaseLibraryApi: ResourceLibraryApi<Codebase, CodebaseVersionsData> = {
  list: async () => (await codebaseApi.list()).codebases,
  listVersions: (id) => codebaseApi.listVersions(id),
  remove: (id) => codebaseApi.delete(id),
  ingestStream: codebaseApi.ingestStream,
};

function codebaseShouldPoll(
  cb: Codebase,
  { versionsById }: { versionsById: Record<string, CodebaseVersionsData | null> },
): boolean {
  return (
    hasActiveBuild(versionsById[cb.id]) ||
    (isIngestingStatus(cb.status) && Boolean(cb.ingest_task_id))
  );
}

export function CodebaseLibrary() {
  const router = useRouter();
  const t = useTranslations("codebase");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Codebase | null>(null);

  const {
    items: codebases,
    setItems: setCodebases,
    versionsById: versionHistories,
    ingestingIds,
    startingId,
    load: loadCodebases,
    remove: removeCodebase,
    watchIngest,
    startTask: runStartTask,
  } = useResourceLibrary<Codebase, CodebaseVersionsData>({
    api: codebaseLibraryApi,
    enabled: Boolean(user),
    loadErrorMessage: t("loadError"),
    shouldPoll: codebaseShouldPoll,
    pollMs: INGEST_POLL_INTERVAL_MS,
    formatIngestError: (ev) => {
      const message =
        typeof ev.data?.error === "string" && ev.data.error.trim() ? ev.data.error : t("indexFailed");
      return t("indexFailedDetail", { error: message });
    },
  });

  const startTask = async (codebase: Codebase, mode: SessionMode = "ask") => {
    const versionId =
      versionHistories[codebase.id]?.active_version_id ??
      codebase.active_version_id ??
      undefined;
    await runStartTask(
      codebase.id,
      async () => {
        const data = await sessionApi.createSession({
          codebase_id: codebase.id,
          codebase_version_id: versionId,
          mode,
        });
        router.push(`/sessions/${data.session_id}`);
      },
      t("startTaskFailed"),
    );
  };

  const handleDeleteConfirm = async () => {
    if (!pendingDelete) return;
    const name = pendingDelete.name;
    try {
      await removeCodebase(pendingDelete.id);
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
            const history = versionHistories[cb.id] ?? null;
            const activeVersionId =
              history?.active_version_id ?? cb.active_version_id;
            const rebuilding = hasActiveBuild(history);
            const ingesting =
              rebuilding ||
              ingestingIds.has(cb.id) ||
              (isIngestingStatus(cb.status) && Boolean(cb.ingest_task_id));
            const canStart = Boolean(activeVersionId) && cb.status !== "failed";
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
                  <div className="basis-full">
                    <ResourceVersionStatus
                      api={codebaseApi}
                      resourceId={cb.id}
                      ns="codebase"
                      controlledHistory={history}
                      onBuildChanged={loadCodebases}
                    />
                  </div>
                  <Button
                    size="sm"
                    disabled={startingId === cb.id || !canStart}
                    onClick={() => void startTask(cb, "ask")}
                  >
                    {startingId === cb.id ? <IconLoading className="size-4 animate-spin" /> : t("startAsk")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={startingId === cb.id || !canStart}
                    onClick={() => void startTask(cb, "agent")}
                  >
                    {t("startAgent")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      if (rebuilding) {
                        await loadCodebases();
                        return;
                      }
                      await codebaseApi.createBuild(cb.id);
                      watchIngest(cb.id);
                      await loadCodebases();
                    }}
                  >
                    <IconRefresh className="mr-1 size-3" />
                    {rebuilding ? t("viewBuild") : t("reanalyze")}
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
