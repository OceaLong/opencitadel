import type { AnalysisFilters, SummaryQuery } from "@/lib/api/types/execution-analysis";

/** Only public query inputs are round-tripped; returned data is always re-authorized. */
export function acceptedQuery(search: URLSearchParams): SummaryQuery {
  const end = new Date();
  const filters: AnalysisFilters = {
    start: new Date(end.getTime() - 7 * 86400000).toISOString(),
    end: end.toISOString(),
  };
  try {
    const parsed: unknown = JSON.parse(search.get("filters") ?? "null");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const saved = parsed as Record<string, unknown>;
      for (const key of [
        "start",
        "end",
        "session",
        "model_revision",
        "configuration_revision",
        "family",
        "tool",
        "status",
        "mode",
        "purpose",
        "batch_id",
      ] as const) {
        if (key in saved && typeof saved[key] === "string") filters[key] = saved[key];
      }
      if (
        "accounting" in saved &&
        ["run", "selected_result", "batch_total"].includes(String(saved.accounting))
      )
        filters.accounting = saved.accounting as AnalysisFilters["accounting"];
      if (
        "comparison_config_version_ids" in saved &&
        Array.isArray(saved.comparison_config_version_ids) &&
        saved.comparison_config_version_ids.length <= 5 &&
        saved.comparison_config_version_ids.every((v) => typeof v === "string")
      )
        filters.comparison_config_version_ids = saved.comparison_config_version_ids;
    }
  } catch {}
  return {
    filters,
    grain: search.get("grain") === "hour" ? "hour" : "day",
    timezone: search.get("timezone") ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    watermark: search.get("watermark") || undefined,
  };
}
export function queryParams(query: SummaryQuery): URLSearchParams {
  const params = new URLSearchParams({
    filters: JSON.stringify(query.filters ?? {}),
    grain: query.grain ?? "day",
    timezone: query.timezone ?? "UTC",
  });
  if (query.watermark) params.set("watermark", query.watermark);
  return params;
}
export function withScroll(path: string, anchor?: string): string {
  const url = new URL(path, window.location.origin);
  url.searchParams.set(
    "scroll",
    String(document.querySelector("main")?.parentElement?.scrollTop ?? 0),
  );
  if (anchor) url.hash = anchor;
  return url.pathname + url.search + url.hash;
}
export function restoreScroll(search: URLSearchParams) {
  const value = Number(search.get("scroll"));
  if (search.has("scroll") && Number.isFinite(value) && value >= 0)
    document.querySelector("main")?.parentElement?.scrollTo({ top: Math.min(value, 10000000) });
  let anchor = window.location.hash.slice(1);
  try {
    anchor = decodeURIComponent(anchor);
  } catch {}
  if (anchor) {
    const element = document.getElementById(anchor);
    const target =
      element?.querySelector<HTMLElement>("button") ??
      element?.querySelector<HTMLElement>("input, select, [tabindex]");
    target?.focus({ preventScroll: true });
  }
}
