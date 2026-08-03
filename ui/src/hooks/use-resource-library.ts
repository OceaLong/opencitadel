"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { SSEEventData, SSEEventHandler } from "@/lib/api/types";

/** The `"error"` branch of the SSE event union, narrowed for message formatting. */
type IngestStreamErrorEvent = Extract<SSEEventData, { type: "error" }>;

/**
 * The slice of a resource's API client the shared library state needs.
 * Both `codebaseApi` and `knowledgeApi` expose `listVersions`/`delete`/
 * `ingestStream` already (via `makeResourceClient` and `createIngestStream`
 * respectively) — callers adapt them into this shape (renaming `delete` to
 * `remove`, unwrapping the `{ codebases }`/`{ knowledge_bases }` envelope of
 * `list()`) rather than the hook branching on resource type.
 */
export type ResourceLibraryApi<TItem, TVersionsData> = {
  list: () => Promise<TItem[]>;
  /**
   * Omit for resources whose version history isn't tracked at the library
   * level — the knowledge library leaves this to `KnowledgeVersionStatus`,
   * which self-fetches per card instead of being handed a controlled
   * `history` prop the way `CodebaseVersionStatus` is.
   */
  listVersions?: (id: string) => Promise<TVersionsData>;
  remove: (id: string) => Promise<void>;
  ingestStream: (
    id: string,
    onEvent: SSEEventHandler,
    onError?: (error: Error) => void,
    eventId?: string,
    onComplete?: () => void,
  ) => () => void;
};

export type UseResourceLibraryOptions<TItem extends { id: string }, TVersionsData> = {
  api: ResourceLibraryApi<TItem, TVersionsData>;
  /** Gate for loading; mirrors each library's `if (!user) { ...; return; }` short-circuit. */
  enabled: boolean;
  /** Run instead of fetching when `enabled` is false, to reset resource-specific state (e.g. KB's `docsByKb`). */
  onReset?: () => void;
  /** Run after a successful load (e.g. KB re-fetching already-expanded document pages). */
  onLoaded?: () => void | Promise<void>;
  loadErrorMessage: string;
  /** Whether an item currently warrants auto-polling `load()` on `pollMs`. */
  shouldPoll: (
    item: TItem,
    ctx: { ingestingIds: Set<string>; versionsById: Record<string, TVersionsData | null> },
  ) => boolean;
  pollMs: number;
  /** KB's `watchIngest` requires a truthy ingest task id before it starts watching; CB always starts. */
  requireIngestTaskId?: boolean;
  formatIngestError: (event: IngestStreamErrorEvent) => string;
  /** Extra side effect when the SSE connection itself fails (KB toasts an error; CB doesn't). */
  onStreamConnectError?: () => void;
};

export type UseResourceLibraryResult<TItem, TVersionsData> = {
  items: TItem[];
  setItems: React.Dispatch<React.SetStateAction<TItem[]>>;
  versionsById: Record<string, TVersionsData | null>;
  ingestingIds: Set<string>;
  startingId: string | null;
  load: () => Promise<void>;
  remove: (id: string) => Promise<void>;
  watchIngest: (id: string, ingestTaskId?: string | null) => void;
  startTask: (id: string, run: () => Promise<void>, errorMessage: string) => Promise<void>;
};

/**
 * Shared list/poll/delete/ingest-watch/start-task state behind the codebase
 * and knowledge base library pages.
 *
 * The two pages render very different JSX (per spec decision, extracting
 * that would hurt readability more than it helps), but the state machine
 * underneath is the same: load the list, poll while something is ingesting,
 * watch a single resource's ingest SSE stream, run a "start session" action
 * with a busy flag, and delete-and-remove-from-list. The genuine differences
 * between the two pages (whether per-item version history is tracked here
 * vs. self-fetched by a child component, the ingest-watch guard, and error
 * message formatting) are threaded through as options instead of the hook
 * branching on resource type.
 */
export function useResourceLibrary<TItem extends { id: string }, TVersionsData = never>({
  api,
  enabled,
  onReset,
  onLoaded,
  loadErrorMessage,
  shouldPoll,
  pollMs,
  requireIngestTaskId,
  formatIngestError,
  onStreamConnectError,
}: UseResourceLibraryOptions<TItem, TVersionsData>): UseResourceLibraryResult<TItem, TVersionsData> {
  const [items, setItems] = useState<TItem[]>([]);
  const [versionsById, setVersionsById] = useState<Record<string, TVersionsData | null>>({});
  const [ingestingIds, setIngestingIds] = useState<Set<string>>(new Set());
  const [startingId, setStartingId] = useState<string | null>(null);
  const ingestCleanupRef = useRef<Map<string, () => void>>(new Map());

  const load = useCallback(async () => {
    if (!enabled) {
      setItems([]);
      onReset?.();
      return;
    }
    try {
      const list = await api.list();
      const listVersions = api.listVersions;
      if (listVersions) {
        const histories = await Promise.all(
          list.map(async (item) => {
            try {
              return [item.id, await listVersions(item.id)] as const;
            } catch {
              return [item.id, null] as const;
            }
          }),
        );
        setVersionsById(Object.fromEntries(histories));
      }
      setItems(list);
      await onLoaded?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : loadErrorMessage);
    }
  }, [enabled, api, onLoaded, onReset, loadErrorMessage]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const hasActive = items.some((item) => shouldPoll(item, { ingestingIds, versionsById }));
    if (!hasActive) return;
    const timer = window.setInterval(() => {
      void load();
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [items, ingestingIds, versionsById, load, pollMs, shouldPoll]);

  useEffect(() => {
    const cleanupMap = ingestCleanupRef.current;
    return () => {
      cleanupMap.forEach((cleanup) => cleanup());
      cleanupMap.clear();
    };
  }, []);

  const watchIngest = useCallback(
    (id: string, ingestTaskId?: string | null) => {
      if (requireIngestTaskId && !ingestTaskId) return;
      if (ingestCleanupRef.current.has(id)) return;
      setIngestingIds((prev) => new Set(prev).add(id));
      const finish = () => {
        setIngestingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        ingestCleanupRef.current.delete(id);
        void load();
      };
      const cleanup = api.ingestStream(
        id,
        (ev) => {
          if (ev.type === "error") {
            toast.error(formatIngestError(ev));
            finish();
            return;
          }
          if (ev.type === "done") {
            finish();
          }
        },
        () => {
          onStreamConnectError?.();
          setIngestingIds((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
          ingestCleanupRef.current.delete(id);
        },
        undefined,
        finish,
      );
      ingestCleanupRef.current.set(id, cleanup);
    },
    [api, load, requireIngestTaskId, formatIngestError, onStreamConnectError],
  );

  const remove = useCallback(
    async (id: string) => {
      await api.remove(id);
      setItems((prev) => prev.filter((item) => item.id !== id));
    },
    [api],
  );

  const startTask = useCallback(
    async (id: string, run: () => Promise<void>, errorMessage: string) => {
      setStartingId(id);
      try {
        await run();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : errorMessage);
      } finally {
        setStartingId(null);
      }
    },
    [],
  );

  return {
    items,
    setItems,
    versionsById,
    ingestingIds,
    startingId,
    load,
    remove,
    watchIngest,
    startTask,
  };
}
