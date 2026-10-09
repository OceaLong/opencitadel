"use client";
import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { type MatrixRow, ResultMatrix } from "@/components/evaluation/result-matrix";

import type { AnalysisSummary } from "@/lib/api/types/execution-analysis";

import { EvaluationCharts } from "./analysis-charts";
export function RetainedEvaluationSeries({
  metrics,
  returnTo,
  returnSearch,
}: {
  metrics: AnalysisSummary["metrics"];
  returnTo: string | ((anchor?: string, values?: Record<string, string>) => string | null);
  returnSearch?: URLSearchParams;
}) {
  const t = useTranslations("analysis");
  const router = useRouter();
  const routeSearch = useSearchParams();
  const search = returnSearch ?? routeSearch;
  const [index, setIndex] = useState(() => {
    const value = Number(search.get("stratum"));
    return Number.isSafeInteger(value) &&
      value >= 0 &&
      value < (metrics.evaluation_series?.length ?? 0)
      ? value
      : 0;
  });
  const [selected, setSelected] = useState<string | null>(search.get("selected_result"));
  const groups = metrics.evaluation_series;
  const series = groups?.[index];
  if (!groups) return <p>{t("retainedScoreUnavailable")}</p>;
  if (!groups.length) return <p>{t("noEvaluationPoints")}</p>;
  const rows = new Map<string, MatrixRow>();
  const configs = new Map<string, string>();
  for (const row of series?.rows ?? []) {
    configs.set(row.config_id, row.config_label);
    const matrix = rows.get(row.case_id) ?? { id: row.case_id, label: row.case_label, results: [] };
    rows.set(row.case_id, {
      ...matrix,
      results: [
        ...matrix.results,
        {
          id: row.id,
          configId: row.config_id,
          repetition: row.repetition,
          attempt: row.attempt,
          runId: row.run_id,
          evaluationRevision: series!.evaluation_revision,
          resultRevision: row.result_revision,
          executionStatus: row.execution_status,
          scoringStatus: row.scoring_status,
          value: row.value == null ? null : Number(row.value),
        },
      ],
    });
  }
  const pick = (id: string, score: boolean) => {
    setSelected(id);
    const row = series?.rows.find((r) => r.id === id);
    const run = score ? row?.score_run_id : row?.run_id;
    const revision = score ? row?.score_run_revision : row?.run_revision;
    const resultRevision = score ? row?.score_result_revision : row?.result_revision;
    if (!row || !run || revision == null || resultRevision == null || !series) return;
    const destination =
      typeof returnTo === "function"
        ? returnTo("evaluation-evidence", { stratum: String(index), selected_result: id })
        : returnTo;
    if (!destination) return;
    const back = new URL(destination, window.location.origin);
    if (typeof returnTo === "string") {
      back.searchParams.set("stratum", String(index));
      back.searchParams.set("selected_result", id);
    }
    router.push(
      `/runs/${encodeURIComponent(run)}?${new URLSearchParams({ batch: series.batch_id, result: row.id, evaluation_run: run, score_revision: String(series.evaluation_revision), score_run_revision: String(revision), result_revision: String(resultRevision), analysis_return: back.pathname + back.search + back.hash })}`,
    );
  };
  return (
    <section id="evaluation-evidence" className="min-w-0 space-y-4">
      <h2 className="text-lg font-semibold">{t("evaluationEvidence")}</h2>
      <label>
        {t("stratum")}
        <select
          className="bg-background block w-full min-w-0 rounded border p-2"
          value={index}
          onChange={(e) => {
            setIndex(Number(e.target.value));
            setSelected(null);
          }}
        >
          {groups.map((g, i) => (
            <option key={i} value={i}>
              {g.source} · {g.dimension} · {g.rubric_id} · {g.batch_id} · {g.evaluation_revision}
            </option>
          ))}
        </select>
      </label>
      {series && (
        <div
          key={JSON.stringify([
            series.identity,
            series.batch_id,
            series.evaluation_revision,
            series.usage_watermark,
          ])}
          className="space-y-4"
        >
          <p>{t("totalCaseCost")}</p>
          <p className="text-sm break-all">
            {t("scoreCut")}: {series.evaluation_revision} · {t("costCut")}: {series.usage_watermark}{" "}
            · {series.identity.map((v) => (Array.isArray(v) ? v.join(",") : v)).join(" · ")}
          </p>
          <EvaluationCharts
            points={series.points}
            labels={Object.fromEntries(configs)}
            source={series.source}
            dimension={series.dimension}
            revision={series.evaluation_revision}
            usageWatermark={series.usage_watermark}
            reviewStatus={
              Object.entries(series.scoring_counts).some(
                ([key, count]) => key === "pending" && count > 0,
              )
                ? "pending"
                : "complete"
            }
            distributionMetadata={series.distribution_metadata}
            qualityCostMetadata={series.quality_cost_metadata}
            onSelectResult={(id) => pick(id, true)}
          />
          <ResultMatrix
            rows={[...rows.values()]}
            configs={[...configs].map(([id, label]) => ({ id, label }))}
            selection={selected}
            onSelectResult={(result) => pick(result.id, false)}
          />
          <details>
            <summary>{t("costComponents")}</summary>
            <div className="max-h-96 overflow-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    <th>{t("result")}</th>
                    <th>{t("subjectCost")}</th>
                    <th>{t("judgeCost")}</th>
                  </tr>
                </thead>
                <tbody>
                  {series.rows.map((row) => (
                    <tr key={row.id}>
                      <td className="break-all">{row.id}</td>
                      {[row.subject_usage, row.judge_usage].map((u, i) => (
                        <td key={i}>
                          {u?.money ?? t("unknown")} <span translate="no">USD</span> ·{" "}
                          {u?.money_known ?? "—"}/{u?.calls ?? "—"} · {t("unresolved")}:{" "}
                          {u?.unresolved ?? "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </section>
  );
}
