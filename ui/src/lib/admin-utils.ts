import { toBcp47 } from "./utils";

export type AdminTimeRange = "7d" | "30d" | "90d" | "all";

export function getAdminDateRange(range: AdminTimeRange): { start_at?: string; end_at?: string } {
  if (range === "all") return {};
  const end = new Date();
  const start = new Date(end);
  const days = range === "7d" ? 7 : range === "30d" ? 30 : 90;
  start.setDate(start.getDate() - days);
  return {
    start_at: start.toISOString(),
    end_at: end.toISOString(),
  };
}

/** Maps the shared admin time-range picker to a `?days=` query value. `"all"`
 * is capped at 365 -- the widest window the governance overview endpoint
 * accepts (`Query(30, ge=1, le=365)` in compliance_routes.py). */
export function getAdminDays(range: AdminTimeRange): number {
  if (range === "all") return 365;
  return range === "7d" ? 7 : range === "30d" ? 30 : 90;
}

export function formatCompactNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

export function formatDateTime(value?: string | null, locale?: string): string {
  if (!value) return "-";
  return new Date(value).toLocaleString(toBcp47(locale), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatShortDate(value: string, locale?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(toBcp47(locale), { month: "2-digit", day: "2-digit" });
}
