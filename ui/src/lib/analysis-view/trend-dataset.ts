import type { components } from "@/lib/api/generated/schema";

import { trendPresentation } from "./chart-contracts";

type Series = components["schemas"]["AnalysisSeries"];
export type TrendRow = {
  timestamp: number;
  observed: boolean;
  success: number | null;
  p50: number | null;
  p95: number | null;
  source: Series | null;
};
/** A missing grid slot is a gap marker, never a measured zero or an extra observation. */
export function buildTrendGroups(series: readonly Series[], grain: "day" | "hour" = "day") {
  const groups = new Map<string, Series[]>();
  for (const row of series) {
    const identity = {
      configuration_revision: row.group.configuration_revision,
      family: row.group.family,
      execution_mode: row.group.execution_mode,
      purpose: row.group.purpose,
    };
    const key = JSON.stringify(identity);
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  const interval = (grain === "hour" ? 1 : 24) * 3600000;
  return [...groups]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, source]) => {
      const sorted = [...source].sort(
        (a, b) => Date.parse(a.group.bucket) - Date.parse(b.group.bucket),
      );
      const rows: TrendRow[] = [];
      for (const item of sorted) {
        const timestamp = Date.parse(item.group.bucket);
        const previous = rows.at(-1);
        // A day can be 23 or 25 hours. Only mark genuine skipped buckets.
        if (previous && timestamp - previous.timestamp > interval * 1.5)
          rows.push({
            timestamp: previous.timestamp + interval,
            observed: false,
            success: null,
            p50: null,
            p95: null,
            source: null,
          });
        const value = (name: string, scale: number) =>
          item.metrics[name]?.value == null ? null : item.metrics[name].value! * scale;
        rows.push({
          timestamp,
          observed: true,
          success: value("success_rate", 100),
          p50: value("latency_p50", 0.001),
          p95: value("latency_p95", 0.001),
          source: item,
        });
      }
      const observed = new Set(sorted.map((r) => r.group.bucket)).size;
      return {
        key,
        identity: sorted[0].group,
        rows,
        observed,
        presentation: trendPresentation(observed),
      };
    });
}
