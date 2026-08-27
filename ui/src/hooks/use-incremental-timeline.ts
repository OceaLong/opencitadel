"use client";

import { useMemo } from "react";

import type { SSEEventData } from "@/lib/api/types";
import { eventsToTimeline, type TimelineItem } from "@/lib/session-events";

import type { Locale } from "@/i18n/routing";

export function useIncrementalTimeline(events: SSEEventData[], locale?: Locale): TimelineItem[] {
  return useMemo(() => eventsToTimeline(events, locale), [events, locale]);
}
