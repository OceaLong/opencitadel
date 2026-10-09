"use client";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { EvaluationError, useEvaluationTask } from "@/components/evaluation/evaluation-boundary";
import { SafeArtifactPreview } from "@/components/session/safe-artifact-preview";
import { Button } from "@/components/ui/button";

import { type DiffDocument, readDiffDocument } from "@/lib/analysis-view/diff";
import { analysisApi } from "@/lib/api/execution-analysis";
import type {
  ComparisonArtifactDiff,
  ComparisonDiffPage,
} from "@/lib/api/types/execution-analysis";

import type { AnalysisAccess } from "./analysis-boundary";
export function ArtifactDiff({
  access,
  comparisonId,
  selection,
}: {
  access: AnalysisAccess;
  comparisonId: string;
  selection: Omit<ComparisonArtifactDiff, "request_id">;
}) {
  const t = useTranslations("analysis");
  const task = useEvaluationTask(access);
  const [job, setJob] = useState<string | null>(null);
  const [status, setStatus] = useState<ComparisonDiffPage["status"] | null>(null);
  const [result, setResult] = useState<DiffDocument | null>(null);
  const read = () => {
    if (!job) return;
    setResult(null);
    void task.run(
      async (o) => {
        let page = await analysisApi.getArtifactDiff(job, undefined, o);
        if (page.status === "queued" || page.status === "running")
          return { status: page.status, result: null };
        const chunks: string[] = [];
        const cursors = new Set<string>();
        for (let i = 0; i < 16; i++) {
          if (page.content != null) chunks.push(page.content);
          if (!page.next_cursor)
            return { status: page.status, result: chunks.length ? readDiffDocument(chunks) : null };
          if (cursors.has(page.next_cursor)) throw new Error("diff_cursor_cycle");
          cursors.add(page.next_cursor);
          page = await analysisApi.getArtifactDiff(job, page.next_cursor, o);
        }
        throw new Error("diff_limit");
      },
      (value) => {
        setStatus(value.status);
        setResult(value.result);
      },
    );
  };
  useEffect(() => {
    if (!job || status === "complete" || status === "partial" || status === "failed") return;
    const timer = setTimeout(read, 1000);
    return () => clearTimeout(timer);
  });
  return (
    <section className="min-w-0 space-y-3">
      <h2 className="text-lg font-semibold">{t("artifactDiff")}</h2>
      <p className="text-sm break-all">
        {selection.left.artifact_id} <span translate="no">v</span>
        {selection.left.version} → {selection.right.artifact_id} <span translate="no">v</span>
        {selection.right.version}
      </p>
      <Button
        disabled={task.pending || !access.canManage}
        onClick={() => {
          setResult(null);
          setJob(null);
          setStatus(null);
          void task.run(
            (o) =>
              analysisApi.createArtifactDiff(
                comparisonId,
                { ...selection, request_id: task.requestId("diff", selection) },
                o,
              ),
            (v) => {
              setJob(v.job_id);
              setStatus("queued");
            },
          );
        }}
      >
        {t("compareArtifacts")}
      </Button>
      <EvaluationError error={task.error} />
      {status && <p role="status">{t(`diffStatus.${status}`)}</p>}
      {result && (
        <>
          <p>
            {result.diff.complete ? t("completeDiff") : t("partialDiff")} ·{" "}
            {result.diff.reason ?? ""} ·{" "}
            {result.diff.content_changed === null
              ? t("unknown")
              : result.diff.content_changed
                ? t("changed")
                : result.diff.complete
                  ? t("unchanged")
                  : t("unknown")}
          </p>
          {result.format === "web" ? (
            <div className="grid min-w-0 gap-3 lg:grid-cols-2">
              <SafeArtifactPreview
                title={t("before")}
                notice={t("safePreview")}
                content={result.diff.before_preview ?? ""}
              />
              <SafeArtifactPreview
                title={t("after")}
                notice={t("safePreview")}
                content={result.diff.after_preview ?? ""}
              />
            </div>
          ) : (
            <pre className="max-h-96 overflow-auto rounded border p-3 text-xs break-words whitespace-pre-wrap">
              {result.diff.content || JSON.stringify(result.diff.operations, null, 2)}
            </pre>
          )}
          <details>
            <summary>{t("viewData")}</summary>
            <pre className="max-h-96 overflow-auto text-xs break-all whitespace-pre-wrap">
              {JSON.stringify(
                {
                  format: result.format,
                  left: result.left,
                  right: result.right,
                  complete: result.diff.complete,
                  reason: result.diff.reason,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      )}
    </section>
  );
}
