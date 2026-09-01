"use client";

import { useCallback, useRef, useState, useSyncExternalStore } from "react";

import { sessionApi } from "@/lib/api/session";
import type { SSEEventData } from "@/lib/api/types";
import {
  createSessionEventStore,
  normalizeEvent,
  normalizeEvents,
  type SessionEventStore,
} from "@/lib/session-events";

const INITIAL_EVENTS_LIMIT = 100;
const RECONNECT_EVENTS_LIMIT = 500;

const EMPTY_EVENTS: SSEEventData[] = [];

export function useSessionEventLog(sessionId: string | null) {
  // 每个 session 一份 store；sessionId 变化时重建（订阅随之切换）。
  const storeHolderRef = useRef<{ id: string | null; store: SessionEventStore } | null>(null);
  if (!storeHolderRef.current || storeHolderRef.current.id !== sessionId) {
    storeHolderRef.current = { id: sessionId, store: createSessionEventStore() };
  }
  const store = storeHolderRef.current.store;

  const subscribe = useCallback((listener: () => void) => store.subscribe(listener), [store]);
  const getSnapshot = useCallback(() => store.getSnapshot(), [store]);
  const getServerSnapshot = useCallback(() => EMPTY_EVENTS, []);
  const events = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [hasEarlierHistory, setHasEarlierHistory] = useState(false);
  const [initialEventsLoaded, setInitialEventsLoaded] = useState(false);
  const earlierCursorRef = useRef<string | null>(null);
  const lastEventIdRef = useRef<string | null>(null);

  const syncLastEventId = useCallback(() => {
    lastEventIdRef.current = store.getLastEventId();
  }, [store]);

  const appendEvent = useCallback(
    (incoming: SSEEventData) => {
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
      const accepted = store.append(event);
      if (accepted) syncLastEventId();
      return accepted;
    },
    [store, syncLastEventId],
  );

  const loadEventsPage = useCallback(async () => {
    if (!sessionId) return;
    try {
      const page = await sessionApi.getSessionEvents(sessionId, {
        latest: true,
        limit: INITIAL_EVENTS_LIMIT,
      });
      earlierCursorRef.current = page.prev_cursor ?? null;
      setHasEarlierHistory(Boolean(page.has_earlier));
      store.replace(normalizeEvents(page.events));
      syncLastEventId();
    } finally {
      setInitialEventsLoaded(true);
    }
  }, [sessionId, store, syncLastEventId]);

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
      for (const event of normalizeEvents(page.events)) {
        if (store.append(event)) syncLastEventId();
      }
      if (!page.next_cursor) break;
      after = page.next_cursor;
    }
  }, [sessionId, store, syncLastEventId, loadEventsPage]);

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
      store.replace([...normalizeEvents(page.events), ...store.getSnapshot()]);
      syncLastEventId();
    } finally {
      setLoadingEarlier(false);
    }
  }, [sessionId, loadingEarlier, store, syncLastEventId]);

  const resetEvents = useCallback(() => {
    earlierCursorRef.current = null;
    setHasEarlierHistory(false);
    setInitialEventsLoaded(false);
    store.clear();
    syncLastEventId();
  }, [store, syncLastEventId]);

  return {
    events,
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
