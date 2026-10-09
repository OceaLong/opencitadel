import type { components } from "@/lib/api/generated/schema";
type Comparison = components["schemas"]["ScoreComparison"];
/** Orient an already computed paired contrast; never recompute its sample or bootstrap. */
export function baselineComparison(row: Comparison, baseline: string | null | undefined) {
  if (!baseline || (row.left !== baseline && row.right !== baseline)) return null;
  const reverse = row.left === baseline;
  const mean = reverse ? row.mean_left : row.mean_right;
  const delta = row.case_count > 0 && row.delta != null ? row.delta * (reverse ? -1 : 1) : null;
  const interval =
    delta != null && row.confidence_interval
      ? reverse
        ? [-row.confidence_interval[1], -row.confidence_interval[0]]
        : row.confidence_interval
      : null;
  return {
    row,
    configuration: reverse ? row.right : row.left,
    baseline,
    delta,
    interval,
    relative: delta != null && mean != null && mean !== 0 ? delta / mean : null,
    relativeState:
      delta == null || mean == null ? "unavailable" : mean === 0 ? "zeroBaseline" : "available",
  };
}
