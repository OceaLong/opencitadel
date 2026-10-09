import { equalCaseValues, summarizeDistribution } from "@/lib/evaluation-view/matrix";
export type EvaluationPoint = {
  excluded?: boolean;
  result_id: string;
  case_id: string;
  config_id: string;
  value: number | null;
  cost_usd: string | null;
};
export function evaluationSeries(points: readonly EvaluationPoint[]) {
  const groups = [...new Set(points.map((p) => p.config_id))].map((config) => {
    const rows = points.filter((p) => p.config_id === config);
    const values = equalCaseValues(
      rows.filter((row) => !row.excluded).map((row) => ({ caseId: row.case_id, value: row.value })),
    );
    const total = new Set(rows.map((r) => r.case_id)).size;
    const excluded = [...new Set(rows.map((r) => r.case_id))].filter((id) =>
      rows.filter((r) => r.case_id === id).every((r) => r.excluded),
    ).length;
    return {
      config,
      excluded,
      values,
      total,
      missing: total - values.length - excluded,
      box: summarizeDistribution(values),
    };
  });
  const complete = points.filter(
    (p) =>
      !p.excluded && p.value !== null && p.cost_usd !== null && Number.isFinite(Number(p.cost_usd)),
  );
  return { groups, complete, scatter: new Set(complete.map((p) => p.case_id)).size >= 12 };
}

/** These gates count observed rows at each chart's declared grain, never grid slots. */
export function trendPresentation(observedBuckets: number): "line" | "discrete" {
  return observedBuckets >= 8 ? "line" : "discrete";
}
export function histogramPresentation(samples: number): "histogram" | "exact" {
  return samples >= 20 ? "histogram" : "exact";
}
export function boxPresentation(cases: number): "box" | "points" {
  return cases >= 5 ? "box" : "points";
}
export function scatterPresentation(completeCases: number): "scatter" | "table" {
  return completeCases >= 12 ? "scatter" : "table";
}
