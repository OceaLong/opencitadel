"use client";

import { useMemo } from "react";

import type { SSEEventData } from "@/lib/api/types";
import { getIncrementalTimeline, type TimelineItem } from "@/lib/session-events";

import type { Locale } from "@/i18n/routing";

/**
 * 从 event-store 的增量 timeline 投影读取（快照来自 store 时复用增量结果，
 * 否则回退纯函数）。接口与旧实现一致：入参 events / locale，出参 TimelineItem[]。
 */
export function useIncrementalTimeline(events: SSEEventData[], locale?: Locale): TimelineItem[] {
  return useMemo(() => getIncrementalTimeline(events, locale), [events, locale]);
}
