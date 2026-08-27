import type { ToolEvent } from "@/lib/api/types";

import type { Locale } from "@/i18n/routing";
import { translate } from "@/i18n/translate";

export function stableId(prefix: string, index: number, suffix: string): string {
  return `${prefix}-${index}-${suffix}`;
}

export function toMillis(ts: number | string | undefined | null): number | undefined {
  if (ts === undefined || ts === null) return undefined;
  let value = typeof ts === "string" ? Date.parse(ts) : ts;
  if (Number.isNaN(value)) return undefined;
  if (typeof ts === "number" && value < 10000000000) {
    value *= 1000;
  }
  return value;
}

export function formatDuration(ms: number | undefined | null): string | undefined {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return undefined;
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/** 将时间戳格式化为相对时间，如 2天前、刚刚 */
function formatTimeLabel(ts: number | string | undefined, locale?: Locale): string | undefined {
  if (ts === undefined || ts === null) return undefined;
  let t = typeof ts === "string" ? parseInt(ts, 10) : ts;
  if (Number.isNaN(t)) return undefined;

  // 后端返回的是秒级时间戳（10位数），需要转为毫秒级（13位数）
  if (t < 10000000000) {
    t = t * 1000;
  }

  const now = Date.now();
  const diff = now - t;
  if (diff < 0) return translate("common.justNow", undefined, locale);
  if (diff < 60 * 1000) return translate("common.justNow", undefined, locale);
  if (diff < 60 * 60 * 1000) {
    return translate(
      "common.relativeTime.minutesAgo",
      { count: Math.floor(diff / (60 * 1000)) },
      locale,
    );
  }
  if (diff < 24 * 60 * 60 * 1000) {
    return translate(
      "common.relativeTime.hoursAgo",
      { count: Math.floor(diff / (60 * 60 * 1000)) },
      locale,
    );
  }
  if (diff < 2 * 24 * 60 * 60 * 1000) return translate("common.dates.yesterday", undefined, locale);
  if (diff < 7 * 24 * 60 * 60 * 1000) {
    return translate(
      "common.relativeTime.daysAgo",
      { count: Math.floor(diff / (24 * 60 * 60 * 1000)) },
      locale,
    );
  }
  if (diff < 30 * 24 * 60 * 60 * 1000) {
    return translate(
      "common.relativeTime.weeksAgo",
      { count: Math.floor(diff / (7 * 24 * 60 * 60 * 1000)) },
      locale,
    );
  }
  return undefined;
}

export function getToolTimeLabel(tool: ToolEvent, locale?: Locale): string | undefined {
  const ts =
    (tool as { timestamp?: number; created_at?: number; ts?: number }).timestamp ??
    (tool as { created_at?: number }).created_at ??
    (tool as { ts?: number }).ts;
  return formatTimeLabel(ts, locale);
}
