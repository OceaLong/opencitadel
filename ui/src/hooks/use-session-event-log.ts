"use client";

import { useCallback, useMemo, useRef, useState, type MutableRefObject } from "react";
import type React from "react";

import { sessionApi } from "@/lib/api/session";
import type { SSEEventData } from "@/lib/api/types";
import { normalizeEvent, normalizeEvents } from "@/lib/session-events";

const INITIAL_EVENTS_LIMIT = 100;

export type EventIndexRefs = {
  eventIds: React.MutableRefObject<Set<string>>;
  messageDelta: React.MutableRefObject<Map<string, number>>;
  reasoningDelta: React.MutableRefObject<Map<string, number>>;
  toolArgsDelta: React.MutableRefObject<Map<string, number>>;
};

export function rebuildEventIndexRefs(events: SSEEventData[], refs: EventIndexRefs) {
  refs.eventIds.current = new Set();
  refs.messageDelta.current.clear();
  refs.reasoningDelta.current.clear();
  refs.toolArgsDelta.current.clear();

  events.forEach((item, index) => {
    const eventId = (item.data as { event_id?: string })?.event_id;
    if (eventId) refs.eventIds.current.add(eventId);
    if (item.type === "message_delta") {
      const streamId = (item.data as { stream_id?: string })?.stream_id;
      if (streamId) refs.messageDelta.current.set(streamId, index);
    }
    if (item.type === "reasoning_delta") {
      const streamId = (item.data as { stream_id?: string })?.stream_id;
      if (streamId) refs.reasoningDelta.current.set(streamId, index);
    }
    if (item.type === "tool_args_delta") {
      const toolCallId = (item.data as { tool_call_id?: string })?.tool_call_id;
      if (toolCallId) refs.toolArgsDelta.current.set(toolCallId, index);
    }
  });
}

export function useSessionEventLog(sessionId: string | null) {
  const eventsRef = useRef<SSEEventData[]>([]);
  const [eventsTick, setEventsTick] = useState(0);
  const flushFrameRef = useRef<number | null>(null);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [hasEarlierHistory, setHasEarlierHistory] = useState(false);
  const earlierCursorRef = useRef<number | null>(null);
  const eventIdSetRef = useRef<Set<string>>(new Set());
  const messageDeltaIndexRef = useRef<Map<string, number>>(new Map());
  const reasoningDeltaIndexRef = useRef<Map<string, number>>(new Map());
  const toolArgsDeltaIndexRef = useRef<Map<string, number>>(new Map());
  const lastEventIdRef = useRef<string | null>(null);

  const indexRefs: EventIndexRefs = {
    eventIds: eventIdSetRef,
    messageDelta: messageDeltaIndexRef,
    reasoningDelta: reasoningDeltaIndexRef,
    toolArgsDelta: toolArgsDeltaIndexRef,
  };

  const bumpEvents = useCallback((next?: SSEEventData[]) => {
    if (next) {
      eventsRef.current = next;
      rebuildEventIndexRefs(next, indexRefs);
    }
    setEventsTick((tick) => tick + 1);
  }, []);

  const scheduleDeltaFlush = useCallback(() => {
    if (flushFrameRef.current !== null) return;
    flushFrameRef.current = window.requestAnimationFrame(() => {
      flushFrameRef.current = null;
      setEventsTick((tick) => tick + 1);
    });
  }, []);

  const mergeDeltaInPlace = useCallback(
    (
      indexMap: MutableRefObject<Map<string, number>>,
      key: string | undefined,
      delta: string | undefined,
    ) => {
      if (!key) return false;
      const idx = indexMap.current.get(key);
      const events = eventsRef.current;
      if (idx === undefined || idx < 0 || idx >= events.length) return false;
      const current = events[idx].data as { delta?: string };
      events[idx] = {
        ...events[idx],
        data: { ...events[idx].data, delta: `${current.delta ?? ""}${delta ?? ""}` },
      } as SSEEventData;
      return true;
    },
    [],
  );

  const appendEvent = useCallback(
    (ev: SSEEventData) => {
      let evToAppend = ev;
      if (
        ev.data &&
        typeof ev.data === "object" &&
        ("event" in ev.data || "type" in ev.data) &&
        "data" in ev.data
      ) {
        const normalized = normalizeEvent(
          ev.data as { event?: string; type?: string; data?: unknown },
        );
        if (normalized) evToAppend = normalized;
      }

      const eventId = (evToAppend.data as { event_id?: string })?.event_id;
      if (eventId) lastEventIdRef.current = eventId;
      if (eventId && eventIdSetRef.current.has(eventId)) return;

      if (evToAppend.type === "message_delta") {
        const data = evToAppend.data as { stream_id?: string; delta?: string };
        if (mergeDeltaInPlace(messageDeltaIndexRef, data.stream_id, data.delta)) {
          scheduleDeltaFlush();
          return;
        }
      }
      if (evToAppend.type === "reasoning_delta") {
        const data = evToAppend.data as { stream_id?: string; delta?: string };
        if (mergeDeltaInPlace(reasoningDeltaIndexRef, data.stream_id, data.delta)) {
          scheduleDeltaFlush();
          return;
        }
      }
      if (evToAppend.type === "tool_args_delta") {
        const data = evToAppend.data as { tool_call_id?: string; delta?: string };
        if (mergeDeltaInPlace(toolArgsDeltaIndexRef, data.tool_call_id, data.delta)) {
          scheduleDeltaFlush();
          return;
        }
      }

      const next = [...eventsRef.current, evToAppend];
      const newIndex = next.length - 1;
      if (eventId) eventIdSetRef.current.add(eventId);
      if (evToAppend.type === "message_delta") {
        const streamId = (evToAppend.data as { stream_id?: string })?.stream_id;
        if (streamId) messageDeltaIndexRef.current.set(streamId, newIndex);
      }
      if (evToAppend.type === "reasoning_delta") {
        const streamId = (evToAppend.data as { stream_id?: string })?.stream_id;
        if (streamId) reasoningDeltaIndexRef.current.set(streamId, newIndex);
      }
      if (evToAppend.type === "tool_args_delta") {
        const toolCallId = (evToAppend.data as { tool_call_id?: string })?.tool_call_id;
        if (toolCallId) toolArgsDeltaIndexRef.current.set(toolCallId, newIndex);
      }
      eventsRef.current = next;
      bumpEvents();
    },
    [bumpEvents, mergeDeltaInPlace, scheduleDeltaFlush],
  );

  const loadEventsPage = useCallback(
    async (includeDebug: boolean) => {
      if (!sessionId) return;
      const eventsPage = await sessionApi.getSessionEvents(sessionId, {
        latest: true,
        limit: INITIAL_EVENTS_LIMIT,
        include_debug: includeDebug,
      });
      const pagedEvents = normalizeEvents(eventsPage.events);
      earlierCursorRef.current = eventsPage.prev_cursor ?? null;
      setHasEarlierHistory(Boolean(eventsPage.has_earlier));
      if (pagedEvents.length > 0) {
        const lastEvId = (pagedEvents[pagedEvents.length - 1]?.data as { event_id?: string })
          ?.event_id;
        if (lastEvId) lastEventIdRef.current = lastEvId;
      }
      bumpEvents(pagedEvents);
    },
    [sessionId, bumpEvents],
  );

  const loadEarlierEvents = useCallback(
    async (includeDebug: boolean) => {
      if (!sessionId || !earlierCursorRef.current || loadingEarlier) return;
      setLoadingEarlier(true);
      try {
        const page = await sessionApi.getSessionEvents(sessionId, {
          before: earlierCursorRef.current,
          limit: INITIAL_EVENTS_LIMIT,
          include_debug: includeDebug,
        });
        const earlierEvents = normalizeEvents(page.events);
        const merged = [...earlierEvents, ...eventsRef.current];
        earlierCursorRef.current = page.prev_cursor ?? null;
        setHasEarlierHistory(Boolean(page.has_earlier));
        bumpEvents(merged);
      } finally {
        setLoadingEarlier(false);
      }
    },
    [sessionId, loadingEarlier, bumpEvents],
  );

  const resetEvents = useCallback(() => {
    eventsRef.current = [];
    earlierCursorRef.current = null;
    eventIdSetRef.current = new Set();
    messageDeltaIndexRef.current.clear();
    reasoningDeltaIndexRef.current.clear();
    toolArgsDeltaIndexRef.current.clear();
    lastEventIdRef.current = null;
    setHasEarlierHistory(false);
    bumpEvents([]);
  }, [bumpEvents]);

  const events = useMemo(() => eventsRef.current, [eventsTick]);

  return {
    events,
    appendEvent,
    loadEventsPage,
    loadEarlierEvents,
    loadingEarlier,
    hasEarlierHistory,
    lastEventIdRef,
    resetEvents,
  };
}
