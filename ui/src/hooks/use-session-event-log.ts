"use client";

import { useCallback, useRef, useState } from "react";

import { sessionApi } from "@/lib/api/session";
import type { SSEEventData } from "@/lib/api/types";
import { normalizeEvent, normalizeEvents } from "@/lib/session-events";

const INITIAL_EVENTS_LIMIT = 100;
const RECONNECT_EVENTS_LIMIT = 500;

export function useSessionEventLog(sessionId: string | null) {
  const eventsRef = useRef<SSEEventData[]>([]);
  const [, setEventsTick] = useState(0);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [hasEarlierHistory, setHasEarlierHistory] = useState(false);
  const [initialEventsLoaded, setInitialEventsLoaded] = useState(false);
  const earlierCursorRef = useRef<string | null>(null);
  const eventIdsRef = useRef<Set<string>>(new Set());
  const lastEventIdRef = useRef<string | null>(null);

  const replaceEvents = useCallback((events: SSEEventData[]) => {
    eventsRef.current = events;
    eventIdsRef.current = new Set(
      events
        .map((event) => event.data.event_id)
        .filter((eventId): eventId is string => Boolean(eventId)),
    );
    lastEventIdRef.current = events.at(-1)?.data.event_id ?? null;
    setEventsTick((tick) => tick + 1);
  }, []);

  const appendEvent = useCallback((incoming: SSEEventData) => {
    let event = incoming;
    if (
      incoming.data &&
      typeof incoming.data === "object" &&
      ("event" in incoming.data || "type" in incoming.data) &&
      "data" in incoming.data
    ) {
      const normalized = normalizeEvent(
        incoming.data as { event?: string; type?: string; data?: unknown },
      );
      if (normalized) event = normalized;
    }
    const eventId = event.data.event_id;
    if (eventId && eventIdsRef.current.has(eventId)) return false;
    if (eventId) {
      eventIdsRef.current.add(eventId);
      lastEventIdRef.current = eventId;
    }
    eventsRef.current = [...eventsRef.current, event];
    setEventsTick((tick) => tick + 1);
    return true;
  }, []);

  const loadEventsPage = useCallback(async () => {
    if (!sessionId) return;
    try {
      const page = await sessionApi.getSessionEvents(sessionId, {
        latest: true,
        limit: INITIAL_EVENTS_LIMIT,
      });
      const events = normalizeEvents(page.events);
      earlierCursorRef.current = page.prev_cursor ?? null;
      setHasEarlierHistory(Boolean(page.has_earlier));
      replaceEvents(events);
    } finally {
      setInitialEventsLoaded(true);
    }
  }, [sessionId, replaceEvents]);

  const syncMissingEvents = useCallback(async () => {
    if (!sessionId) return;
    let after = lastEventIdRef.current;
    if (after == null) {
      await loadEventsPage();
      return;
    }
    while (true) {
      const page = await sessionApi.getSessionEvents(sessionId, {
        after,
        limit: RECONNECT_EVENTS_LIMIT,
      });
      for (const event of normalizeEvents(page.events)) appendEvent(event);
      if (!page.next_cursor) break;
      after = page.next_cursor;
    }
  }, [sessionId, appendEvent, loadEventsPage]);

  const loadEarlierEvents = useCallback(async () => {
    if (!sessionId || !earlierCursorRef.current || loadingEarlier) return;
    setLoadingEarlier(true);
    try {
      const page = await sessionApi.getSessionEvents(sessionId, {
        before: earlierCursorRef.current,
        limit: INITIAL_EVENTS_LIMIT,
      });
      earlierCursorRef.current = page.prev_cursor ?? null;
      setHasEarlierHistory(Boolean(page.has_earlier));
      replaceEvents([...normalizeEvents(page.events), ...eventsRef.current]);
    } finally {
      setLoadingEarlier(false);
    }
  }, [sessionId, loadingEarlier, replaceEvents]);

  const resetEvents = useCallback(() => {
    earlierCursorRef.current = null;
    setHasEarlierHistory(false);
    setInitialEventsLoaded(false);
    replaceEvents([]);
  }, [replaceEvents]);

  return {
    events: eventsRef.current,
    appendEvent,
    loadEventsPage,
    syncMissingEvents,
    loadEarlierEvents,
    loadingEarlier,
    hasEarlierHistory,
    initialEventsLoaded,
    lastEventIdRef,
    resetEvents,
  };
}
