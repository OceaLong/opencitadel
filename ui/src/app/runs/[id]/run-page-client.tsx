"use client";
import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { AnalysisReturnLink } from "@/components/analysis/return-link";
import { EvaluationResultEntry } from "@/components/evaluation/result-entry";
import { DetailPanel } from "@/components/execution/detail-panel";
import { PlaybackControls } from "@/components/execution/playback-controls";
import { TaskView } from "@/components/execution/task-view";
import { TraceView } from "@/components/execution/trace-view";
import { WorkbenchShell } from "@/components/execution/workbench-shell";
import { Button } from "@/components/ui/button";

import { useExecutionWorkbench } from "@/hooks/use-execution-workbench";
import {
  parseSelection,
  removeInitialMessage,
  serializeSelection,
} from "@/lib/execution-view/url-state";

export function RunPageClient({ runId }: { runId: string }) {
  const t = useTranslations("executionWorkbench");
  const router = useRouter();
  const search = useSearchParams().toString();
  const workbench = useExecutionWorkbench();
  const { selection, view: page } = workbench;
  const run = selection.runId === runId ? page?.run : undefined;
  const source = run?.source;
  const sessionId =
    source?.session_id ?? (source?.entity_type === "session" ? source.entity_id : null);
  useEffect(() => {
    const target = { ...parseSelection(search).selection, runId };
    if (sessionId)
      router.replace(
        removeInitialMessage(
          `/sessions/${encodeURIComponent(sessionId)}?${serializeSelection(target, search)}${window.location.hash}`,
        ),
        { scroll: false },
      );
    else if (parseSelection(search).selection.runId !== runId)
      router.replace(
        `/runs/${encodeURIComponent(runId)}?${serializeSelection(target, search)}${window.location.hash}`,
        { scroll: false },
      );
  }, [runId, search, sessionId, router]);
  const selectStep = (stepId: string) =>
    workbench.setSelection({
      ...selection,
      stepId,
      panel: "overview",
      artifactId: null,
      version: null,
      citationId: null,
    });
  const sourceHref =
    source?.entity_type === "patrol_run"
      ? `/patrol-runs/${encodeURIComponent(source.entity_id)}`
      : source?.entity_type === "resource_build"
        ? "/knowledge"
        : source?.entity_type === "scheduled_job"
          ? "/automation"
          : source?.entity_type === "patrol_pack_validation"
            ? "/patrols"
            : null;
  return (
    <WorkbenchShell
      playback={
        <PlaybackControls
          at={selection.at}
          latestAvailable={workbench.latestAvailable}
          start={workbench.playbackRun?.admitted_at}
          currentTime={workbench.playbackRun?.as_of}
          coverage={workbench.playbackRun?.completeness}
          onSeekTime={workbench.seekTime}
          onSeekEvent={workbench.seekEvent}
          onReturnLive={workbench.returnToLive}
        />
      }
      selection={selection}
      onSelectionChange={workbench.setSelection}
      layout={workbench.layout}
      onLayoutChange={workbench.setLayout}
      runs={run ? [run] : []}
      header={
        <div className="flex flex-wrap gap-2">
          <span>{t("readOnlyRun")}</span>
          <EvaluationResultEntry runId={runId} />
          <AnalysisReturnLink />
          {sourceHref && (
            <a className="underline" href={sourceHref}>
              {t("openSource")}
            </a>
          )}
        </div>
      }
      notice={
        workbench.targetUnavailable ? (
          <p role="status">
            {t("targetUnavailable")}
            <Button variant="ghost" onClick={workbench.dismissTargetNotice}>
              {t("dismiss")}
            </Button>
          </p>
        ) : undefined
      }
      view={
        run && page ? (
          selection.view === "task" ? (
            <TaskView
              run={run}
              steps={page.steps}
              approvals={page.approvals}
              artifacts={page.artifacts}
              onSelectStep={selectStep}
              onSelectArtifact={(artifactId, version) =>
                workbench.setSelection({
                  ...selection,
                  artifactId,
                  version,
                  panel: "artifact",
                  stepId: null,
                  citationId: null,
                })
              }
              canApprove={false}
              onOpenApproval={() => {}}
            />
          ) : (
            <TraceView
              key={`${selection.runId}:${page.at}:${page.revision}`}
              steps={workbench.trace?.steps ?? page.steps}
              selection={selection.stepId}
              onSelectStep={selectStep}
              asOf={page.run.as_of}
              exhausted={workbench.trace?.exhausted ?? !page.next_cursor}
              complete={workbench.trace?.complete ?? page.run.completeness.state === "complete"}
              error={["error", "conflict", "rebuilding"].includes(workbench.loadState)}
              onRetry={workbench.refresh}
            />
          )
        ) : (
          <div role="status" className="space-y-3 p-4">
            <p>{t(`loadState.${workbench.loadState}`)}</p>
            <Button onClick={workbench.refresh}>{t("refreshRuns")}</Button>
          </div>
        )
      }
      detail={
        <DetailPanel
          run={run ?? null}
          page={page}
          detail={workbench.detail}
          selection={selection}
          onReturnLive={workbench.returnToLive}
          onChanged={workbench.refresh}
          onRevoked={workbench.revokeContent}
          onSelectionChange={workbench.setSelection}
        />
      }
      detailOpen={Boolean(
        selection.panel || selection.stepId || selection.artifactId || selection.citationId,
      )}
      onCloseDetail={() =>
        workbench.setSelection({
          ...selection,
          stepId: null,
          panel: null,
          artifactId: null,
          version: null,
          citationId: null,
        })
      }
    />
  );
}
