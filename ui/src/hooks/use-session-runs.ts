"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { SSEEventData } from "@/lib/api/types";
import type { RunViewPage } from "@/lib/api/types/execution-view";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

type ListState = "loading" | "ready" | "empty" | "updating" | "forbidden" | "error";
/** Only persisted source-scoped cohorts supply defaults. Explicit URL choices always win. */
export function useSessionRuns(
  sessionId: string,
  events: SSEEventData[] = [],
  admissionPending = false,
  admissionRevision = 0,
) {
  // Only persisted user messages represent new admitted turns; late old usage/status must not move this hint backwards.
  const admission = events.findLast(
    (event) =>
      event.type === "message" &&
      event.data.role === "user" &&
      event.data.persist !== false &&
      Boolean(event.data.run_id),
  );
  const admittedRunId = admission?.data.run_id;
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const search = useSearchParams().toString();
  const pathname = usePathname();
  const router = useRouter();
  const nav = useRef({ search, pathname, router });
  useLayoutEffect(() => {
    nav.current = { search, pathname, router };
  }, [search, pathname, router]);
  const enabled = !loading && user?.id === scope?.userId && Boolean(user);
  const key = JSON.stringify([
    enabled,
    scope,
    scopeRevision,
    sessionId,
    admittedRunId,
    admissionPending,
    admissionRevision,
  ]);
  const [refreshId, setRefreshId] = useState(0);
  const [pagination, setPagination] = useState<{ key: string; cursor: string } | null>(null);
  const cursor = pagination?.key === key ? pagination.cursor : undefined;
  const [snapshot, setSnapshot] = useState<{
    key: string;
    page: RunViewPage | null;
    state: ListState;
    refreshId: number;
  } | null>(null);
  const generation = useRef(0);
  useLayoutEffect(() => {
    const counter = generation;
    counter.current++;
    return () => {
      counter.current++;
    };
  }, [key, refreshId, cursor]);
  useEffect(() => {
    if (!enabled || admissionPending) return;
    const own = ++generation.current;
    const abort = new AbortController();
    const current = () => generation.current === own && !abort.signal.aborted;
    let retry: ReturnType<typeof setTimeout> | undefined;
    // A continuation belongs to this exact captured cohort. Its tail cannot clear
    // a first-page admission/completeness fence; only a fresh first page can.
    const prior =
      cursor && snapshot?.key === key && snapshot.refreshId === refreshId ? snapshot : null;
    void executionViewApi
      .listRuns(
        { source_entity_type: "session", source_entity_id: sessionId, limit: 50, cursor },
        { signal: abort.signal, workspaceId: scope?.workspaceId ?? "" },
      )
      .then((page) => {
        if (!current()) return;
        const items = prior?.page ? [...prior.page.items, ...page.items] : page.items;
        // Partial content describes confirmed rows, not uncertain source membership.
        // Unexplained partial responses and admission/pagination fences still wait.
        const explainedContentGap =
          page.completeness.state === "partial" &&
          Boolean(
            page.completeness.missing_fields?.length || page.completeness.missing_intervals?.length,
          );
        const updating =
          (page.completeness.state !== "complete" && !explainedContentGap) ||
          Boolean(cursor && prior?.state !== "ready") ||
          Boolean(admittedRunId && !items.some((run) => run.run_id === admittedRunId));
        if (updating)
          retry = setTimeout(() => {
            if (current()) {
              setPagination(null);
              setRefreshId((value) => value + 1);
            }
          }, 1000);
        setSnapshot({
          key,
          refreshId,
          page: { ...page, items },
          state: updating ? "updating" : items.length ? "ready" : "empty",
        });
        const latest = nav.current;
        const params = new URLSearchParams(latest.search);
        if (!updating && !params.has("run") && page.items[0] && !cursor) {
          params.set("run", page.items[0].run_id);
          latest.router.replace(`${latest.pathname}?${params}${window.location.hash}`, {
            scroll: false,
          });
        }
      })
      .catch((error) => {
        if (current() && error instanceof ApiError && error.code === 503)
          retry = setTimeout(() => {
            if (current()) {
              setPagination(null);
              setRefreshId((value) => value + 1);
            }
          }, 1000);
        if (current())
          setSnapshot({
            key,
            refreshId,
            page: null,
            state:
              error instanceof ApiError && error.code === 403
                ? "forbidden"
                : error instanceof ApiError && error.code === 503
                  ? "updating"
                  : "error",
          });
      });
    return () => {
      abort.abort();
      clearTimeout(retry);
    };
    // The key includes every authenticated source identity field.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, refreshId, cursor]);
  const visible =
    enabled && !admissionPending && snapshot?.key === key && snapshot.refreshId === refreshId
      ? snapshot
      : null;
  const refresh = useCallback(() => {
    setPagination(null);
    setRefreshId((value) => value + 1);
  }, []);
  return {
    items: visible?.page?.items ?? [],
    state: visible?.state ?? "loading",
    nextCursor: visible?.page?.next_cursor ?? null,
    loadMore: () => {
      if (visible?.page?.next_cursor) setPagination({ key, cursor: visible.page.next_cursor });
    },
    refresh,
  };
}
