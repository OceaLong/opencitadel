"use client";
/* eslint-disable react-hooks/refs, react-hooks/set-state-in-effect -- incremental timeline keeps prior state in refs */

import { useEffect, useRef, useState } from "react";

import type { Locale } from "@/i18n/routing";
import type { SSEEventData } from "@/lib/api/types";
import type { TimelineItem } from "@/lib/session-events";
import { eventsToTimeline, patchTimelineForDeltaEvent } from "@/lib/session-events";

const TRANSIENT_EVENT_TYPES = new Set(["message_delta", "reasoning_delta", "tool_args_delta"]);

export function useIncrementalTimeline(events: SSEEventData[], locale?: Locale): TimelineItem[] {
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const prevEventsRef = useRef<SSEEventData[]>([]);
  const timelineRef = useRef<TimelineItem[]>([]);

  useEffect(() => {
    const prev = prevEventsRef.current;
    if (events.length === 0) {
      timelineRef.current = [];
      setTimeline([]);
      prevEventsRef.current = [];
      return;
    }

    const rebuild = (nextEvents: SSEEventData[]) => {
      const nextTimeline = eventsToTimeline(nextEvents, locale);
      timelineRef.current = nextTimeline;
      setTimeline(nextTimeline);
      prevEventsRef.current = nextEvents;
    };

    if (prev.length === 0 || events.length < prev.length || events[0] !== prev[0]) {
      rebuild(events);
      return;
    }

    if (events.length === prev.length) {
      const last = events[events.length - 1];
      if (TRANSIENT_EVENT_TYPES.has(last.type)) {
        const patched = patchTimelineForDeltaEvent(timelineRef.current, last);
        if (patched) {
          timelineRef.current = patched;
          setTimeline(patched);
          prevEventsRef.current = events;
          return;
        }
      }
      rebuild(events);
      return;
    }

    let nextTimeline = timelineRef.current;
    let incrementalOk = true;
    for (let i = prev.length; i < events.length; i++) {
      const ev = events[i];
      if (TRANSIENT_EVENT_TYPES.has(ev.type)) {
        const patched = patchTimelineForDeltaEvent(nextTimeline, ev);
        if (!patched) {
          incrementalOk = false;
          break;
        }
        nextTimeline = patched;
      } else {
        incrementalOk = false;
        break;
      }
    }

    if (incrementalOk) {
      timelineRef.current = nextTimeline;
      setTimeline(nextTimeline);
      prevEventsRef.current = events;
      return;
    }

    rebuild(events);
  }, [events, locale]);

  return timeline;
}
