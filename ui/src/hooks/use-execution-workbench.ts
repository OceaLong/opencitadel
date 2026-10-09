"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type {
  StepDetail,
  TimelineQuery,
  TimelineView,
  ViewPage,
} from "@/lib/api/types/execution-view";
import { clientDataScopeKey } from "@/lib/data/client-data-scope";
import { subscribeExecutionInvalidations } from "@/lib/execution-view/event-subscription";
import {
  readLayout,
  saveLayout,
  type WorkbenchLayout,
} from "@/lib/execution-view/layout-preferences";
import { canSeekTime, reconcileVisibleSelection } from "@/lib/execution-view/playback-selection";
import type { WorkbenchSelection } from "@/lib/execution-view/state";
import { loadTracePages, type TraceData } from "@/lib/execution-view/trace-loader";
import { parseSelection, serializeSelection } from "@/lib/execution-view/url-state";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

export type WorkbenchLoadState =
  | "idle"
  | "loading"
  | "refreshing"
  | "ready"
  | "forbidden"
  | "conflict"
  | "unavailable"
  | "rebuilding"
  | "error";
type Snapshot = {
  key: string;
  boundaryKey: string;
  selection: WorkbenchSelection;
  view: ViewPage | null;
  detail: StepDetail | null;
  loadState: WorkbenchLoadState;
  error: unknown;
  targetUnavailable: boolean;
  trace?: TraceData;
};
const conflict = () => new ApiError(409, "revision_conflict", { code: "revision_conflict" });

/** URL owns location. This hook owns only a single authorized, revision-consistent response. */
export function useExecutionWorkbench() {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const search = useSearchParams().toString();
  const pathname = usePathname();
  const router = useRouter();
  const navigation = useRef({ search, pathname, router });
  useLayoutEffect(() => {
    navigation.current = { search, pathname, router };
  }, [search, pathname, router]);
  const parsed = useMemo(() => parseSelection(search), [search]);
  const scopeKey = !loading && user && scope?.userId === user.id ? clientDataScopeKey(scope) : null;
  const [layoutSnapshot, setLayoutSnapshot] = useState<{
    key: string;
    value: WorkbenchLayout;
  } | null>(null);
  const storedLayout = useMemo(
    () => (scopeKey && scope ? readLayout(scope) : {}),
    [scopeKey, scope],
  );
  const layout = layoutSnapshot?.key === scopeKey ? layoutSnapshot.value : storedLayout;
  const requested = useMemo(
    () =>
      new URLSearchParams(search).has("view")
        ? parsed.selection
        : { ...parsed.selection, view: layout.view ?? "task" },
    [search, parsed.selection, layout.view],
  );
  const key = JSON.stringify([scopeKey, scopeRevision, requested]);
  const boundaryKey = JSON.stringify([scopeKey, scopeRevision, requested.runId, requested.at]);
  const [refreshRevision, setRefreshRevision] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [missingNotice, setMissingNotice] = useState<{
    boundaryKey: string;
    selection: string;
  } | null>(null);
  const [seekIntent, setSeekIntent] = useState<{ key: string; error: unknown } | null>(null);
  const seekGeneration = useRef(0);
  const seekController = useRef<AbortController | null>(null);
  const seekTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useLayoutEffect(() => {
    seekGeneration.current += 1;
    seekController.current?.abort();
    if (seekTimer.current) clearTimeout(seekTimer.current);
    return () => {
      seekGeneration.current += 1;
      seekController.current?.abort();
      if (seekTimer.current) clearTimeout(seekTimer.current);
    };
  }, [key]);
  const requestBusy = useRef(false);
  const [latestMetadata, setLatestMetadata] = useState<{
    authority: string;
    latest: string | null;
  } | null>(null);
  const [deniedAuthority, setDeniedAuthority] = useState<string | null>(null);
  const [subscriptionRetry, setSubscriptionRetry] = useState(0);
  const authority = JSON.stringify([scopeKey, scopeRevision, requested.runId]);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  // Layout cleanup fences already-resolved promise continuations before passive effects run.
  useLayoutEffect(() => {
    generation.current += 1;
    controller.current?.abort();
    return () => {
      generation.current += 1;
      controller.current?.abort();
    };
  }, [key, refreshRevision]);

  useEffect(() => {
    setMissingNotice((previous) =>
      previous?.boundaryKey === boundaryKey && previous.selection === serializeSelection(requested)
        ? previous
        : null,
    );
    if (!scopeKey || !requested.runId) {
      setSnapshot(null);
      return;
    }
    requestBusy.current = true;
    const ownGeneration = ++generation.current;
    const abort = new AbortController();
    controller.current = abort;
    const requestOptions = { signal: abort.signal, workspaceId: scope?.workspaceId ?? "" };
    const current = () => generation.current === ownGeneration && !abort.signal.aborted;
    setSnapshot((previous) =>
      current()
        ? {
            key,
            boundaryKey,
            selection: requested,
            view: previous?.boundaryKey === boundaryKey ? previous.view : null,
            detail: previous?.key === key ? previous.detail : null,
            loadState:
              previous?.boundaryKey === boundaryKey && previous.view ? "refreshing" : "loading",
            error: null,
            targetUnavailable: false,
          }
        : previous,
    );
    void (async () => {
      try {
        const view = await executionViewApi.getView(
          requested.runId,
          { at: requested.at },
          requestOptions,
        );
        if (!current()) return;
        if (
          view.run.run_id !== requested.runId ||
          view.run.projection_revision !== view.revision ||
          (requested.at !== null && view.at !== requested.at)
        )
          throw conflict();
        let detail: StepDetail | null = null;
        let selection = requested;
        let targetUnavailable = false;
        if (requested.stepId) {
          if (!view.at) throw conflict();
          try {
            detail = await executionViewApi.getStep(
              requested.runId,
              requested.stepId,
              { at: view.at },
              requestOptions,
            );
            if (!current()) return;
            if (
              detail.run_id !== view.run.run_id ||
              detail.at !== view.at ||
              detail.projection_revision !== view.revision
            )
              throw conflict();
            // The point-read resolves only the explicit replacement relation at this exact cut.
            selection = { ...selection, stepId: detail.step_id };
          } catch (error) {
            if (!current()) return;
            if (!(error instanceof ApiError) || error.code !== 404) throw error;
            if (view.run.completeness?.state === "complete")
              selection = reconcileVisibleSelection(selection, new Set(), null);
            targetUnavailable = true;
          }
        }
        if (!current()) return;
        const artifacts = detail
          ? [...(detail.artifact_refs ?? [])]
          : [...(view.artifacts ?? []), ...view.steps.flatMap((item) => item.artifact_refs ?? [])];
        const citations = detail
          ? [...(detail.citation_refs ?? [])]
          : [...view.steps.flatMap((item) => item.citation_refs ?? [])];
        const artifactExists = () =>
          !selection.artifactId ||
          artifacts.some(
            (item) =>
              item.artifact_id === selection.artifactId &&
              (selection.version === null || item.version === selection.version),
          );
        const citationExists = () =>
          !selection.citationId ||
          citations.some((item) => item.citation_id === selection.citationId);
        let cursor = detail ? null : view.next_cursor;
        const seen = new Set<string>();
        // A paged-out object is not a missing object. Resolve only as much as its locator needs.
        while (cursor && (!artifactExists() || !citationExists())) {
          if (!view.at || seen.has(cursor)) throw conflict();
          seen.add(cursor);
          const page = await executionViewApi.listSteps(
            requested.runId,
            { at: view.at, revision: view.revision, cursor },
            requestOptions,
          );
          if (!current()) return;
          if (page.at !== view.at || page.revision !== view.revision) throw conflict();
          artifacts.push(...page.items.flatMap((item) => item.artifact_refs ?? []));
          citations.push(...page.items.flatMap((item) => item.citation_refs ?? []));
          cursor = page.next_cursor;
        }
        if (!artifactExists()) {
          if (view.run.completeness?.state === "complete")
            selection = {
              ...selection,
              artifactId: null,
              version: null,
              panel: selection.panel === "artifact" ? null : selection.panel,
            };
          targetUnavailable = true;
        }
        if (!citationExists()) {
          if (view.run.completeness?.state === "complete")
            selection = {
              ...selection,
              citationId: null,
              panel: selection.panel === "source" ? null : selection.panel,
            };
          targetUnavailable = true;
        }
        if (!current()) return;
        if (targetUnavailable) {
          setMissingNotice({ boundaryKey, selection: serializeSelection(selection) });
        }
        if (serializeSelection(selection) !== serializeSelection(requested)) {
          const latest = navigation.current;
          latest.router.replace(
            `${latest.pathname}?${serializeSelection(selection, latest.search)}${window.location.hash}`,
            { scroll: false },
          );
        }
        setSnapshot((previous) =>
          current()
            ? {
                key,
                boundaryKey,
                selection,
                view,
                detail,
                loadState: "ready",
                error: null,
                targetUnavailable,
              }
            : previous,
        );
        if (requested.view === "debug") {
          await loadTracePages(view, detail, requestOptions, (trace) => {
            if (!current()) return;
            setSnapshot((previous) =>
              current() && previous?.key === key ? { ...previous, trace } : previous,
            );
          });
        }
      } catch (error) {
        if (!current() || (error instanceof Error && error.name === "AbortError")) return;
        const code = error instanceof ApiError ? error.code : null;
        if (code === 403 || code === 401) setDeniedAuthority(authority);
        const unavailable =
          code === 404 ||
          (error instanceof ApiError &&
            (error.data as { code?: string } | null)?.code === "resource_unavailable");
        const loadState: WorkbenchLoadState =
          code === 403
            ? "forbidden"
            : unavailable
              ? "unavailable"
              : code === 409
                ? "conflict"
                : code === 503
                  ? "rebuilding"
                  : "error";
        setSnapshot((previous) =>
          current()
            ? {
                key,
                boundaryKey,
                selection: requested,
                view:
                  code === 403 || unavailable
                    ? null
                    : previous?.boundaryKey === boundaryKey
                      ? previous.view
                      : null,
                detail:
                  code === 403 || unavailable
                    ? null
                    : previous?.key === key
                      ? previous.detail
                      : null,
                trace:
                  code === 403 || unavailable
                    ? undefined
                    : previous?.key === key
                      ? previous.trace
                      : undefined,
                loadState,
                error,
                targetUnavailable: false,
              }
            : previous,
        );
      } finally {
        if (current()) requestBusy.current = false;
      }
    })();
    return () => {
      abort.abort();
      if (generation.current === ownGeneration) generation.current += 1;
    };
    // `key` includes the complete normalized selection and authenticated scope revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, refreshRevision]);

  const setSelection = useCallback(
    (selection: WorkbenchSelection) => {
      setMissingNotice(null);
      if (serializeSelection(selection) === serializeSelection(requested)) return;
      seekGeneration.current += 1;
      seekController.current?.abort();
      if (seekTimer.current) clearTimeout(seekTimer.current);
      setSeekIntent(
        selection.at !== requested.at || selection.runId !== requested.runId
          ? { key, error: null }
          : null,
      );
      generation.current += 1;
      controller.current?.abort();
      if (scopeKey && scope) {
        setLayoutSnapshot({
          key: scopeKey,
          value: saveLayout(scope, {
            ...layout,
            view: selection.view,
            contextCollapsed: readLayout(scope).contextCollapsed,
          }),
        });
      }
      const next = parseSelection(serializeSelection(selection, search)).selection;
      router.push(`${pathname}?${serializeSelection(next, search)}${window.location.hash}`, {
        scroll: false,
      });
    },
    [pathname, router, search, scopeKey, scope, layout, requested, key],
  );
  const visible = scopeKey && snapshot?.key === key ? snapshot : null;
  const confirmedView = scopeKey && snapshot?.boundaryKey === boundaryKey ? snapshot.view : null;
  const selection = visible?.selection ?? requested;
  const refresh = useCallback(() => {
    seekGeneration.current += 1;
    seekController.current?.abort();
    if (seekTimer.current) clearTimeout(seekTimer.current);
    setSeekIntent(null);
    setDeniedAuthority(null);
    if (deniedAuthority) setSubscriptionRetry((value) => value + 1);
    requestBusy.current = true;
    generation.current += 1;
    controller.current?.abort();
    setRefreshRevision((value) => value + 1);
  }, [deniedAuthority]);
  const seek = useCallback(
    (query: Partial<TimelineQuery>, immediate: boolean) => {
      if (!scopeKey || !requested.runId) return;
      generation.current += 1;
      controller.current?.abort();
      const own = ++seekGeneration.current;
      seekController.current?.abort();
      if (seekTimer.current) clearTimeout(seekTimer.current);
      setSeekIntent({ key, error: null });
      const abort = new AbortController();
      seekController.current = abort;
      const current = () => own === seekGeneration.current && !abort.signal.aborted;
      const resolve = async () => {
        try {
          const coverage = (visible?.view ?? confirmedView)?.run.completeness;
          if (query.target_time && coverage && !canSeekTime(query.target_time, coverage))
            throw new ApiError(404, "resource_unavailable", { code: "resource_unavailable" });
          const timeline: TimelineView = await executionViewApi.getTimeline(
            requested.runId,
            {
              start: "0001-01-01T00:00:00Z",
              end: "9999-12-31T23:59:59Z",
              ...query,
            },
            { signal: abort.signal, workspaceId: scope?.workspaceId ?? "" },
          );
          if (!current()) return;
          if (timeline.run_id !== requested.runId) throw conflict();
          if (query.target_time && !canSeekTime(query.target_time, timeline.completeness))
            throw new ApiError(404, "resource_unavailable", { code: "resource_unavailable" });
          if (!timeline.at)
            throw new ApiError(404, "resource_unavailable", { code: "resource_unavailable" });
          setSelection({ ...requested, at: timeline.at });
          if (timeline.at === requested.at) {
            setSeekIntent(null);
            refresh();
          }
        } catch (error) {
          if (current()) {
            if (error instanceof ApiError && (error.code === 401 || error.code === 403)) {
              setSnapshot(null);
              setLatestMetadata(null);
              setDeniedAuthority(authority);
            }
            setSeekIntent({ key, error });
          }
        }
      };
      if (immediate) void resolve();
      else
        seekTimer.current = setTimeout(() => {
          void resolve();
        }, 150);
    },
    [
      scopeKey,
      requested,
      key,
      scope,
      visible?.view,
      confirmedView,
      setSelection,
      refresh,
      authority,
    ],
  );
  const seekTime = useCallback(
    (time: string, release = false) => seek({ target_time: time }, release),
    [seek],
  );
  const seekEvent = useCallback(
    (direction: "before" | "after") => {
      const anchor = requested.at ?? (visible?.view ?? confirmedView)?.at;
      if (anchor) seek({ anchor_at: anchor, direction }, true);
    },
    [requested.at, visible?.view, confirmedView, seek],
  );
  const returnToLive = useCallback(() => {
    seekGeneration.current += 1;
    seekController.current?.abort();
    if (seekTimer.current) clearTimeout(seekTimer.current);
    setSeekIntent(null);
    if (selection.at === null) refresh();
    else setSelection({ ...selection, at: null });
  }, [refresh, selection, setSelection]);
  const setLayout = useCallback(
    (next: WorkbenchLayout) => {
      if (scopeKey && scope)
        setLayoutSnapshot({
          key: scopeKey,
          value: saveLayout(scope, {
            ...next,
            contextCollapsed: readLayout(scope).contextCollapsed,
          }),
        });
    },
    [scopeKey, scope],
  );
  const seeking = seekIntent?.key === key;
  const seekErrorCode = seekIntent?.error instanceof ApiError ? seekIntent.error.code : null;
  const seekLoadState: WorkbenchLoadState = !seekIntent?.error
    ? "loading"
    : seekErrorCode === 409
      ? "conflict"
      : seekErrorCode === 503
        ? "rebuilding"
        : seekErrorCode === 404
          ? "unavailable"
          : "error";
  const denied = deniedAuthority === authority;
  const subscriptionState = useRef({ requested, seeking, refresh });
  useLayoutEffect(() => {
    subscriptionState.current = { requested, seeking, refresh };
  }, [requested, seeking, refresh]);
  // Subscription identity excludes at and selected step: one feed per authorized Run.
  useLayoutEffect(() => {
    if (!scopeKey || !requested.runId || denied) return;
    const abort = new AbortController();
    let dirty = false;
    let watermarkBusy = false;
    const revoke = () => {
      if (abort.signal.aborted) return;
      abort.abort();
      generation.current += 1;
      controller.current?.abort();
      seekGeneration.current += 1;
      seekController.current?.abort();
      setSnapshot(null);
      setDeniedAuthority(authority);
      setLatestMetadata(null);
    };
    const stop = subscribeExecutionInvalidations(
      requested.runId,
      scope?.workspaceId ?? "",
      () => {
        if (!abort.signal.aborted) dirty = true;
      },
      revoke,
    );
    const timer = setInterval(() => {
      const state = subscriptionState.current;
      if (abort.signal.aborted || !dirty || state.seeking) return;
      if (state.requested.at === null) {
        if (requestBusy.current) return;
        dirty = false;
        state.refresh();
        return;
      }
      if (watermarkBusy) return;
      dirty = false;
      watermarkBusy = true;
      void executionViewApi
        .getTimeline(
          requested.runId,
          { start: "0001-01-01T00:00:00Z", end: "9999-12-31T23:59:59Z", bucket_count: 1 },
          { signal: abort.signal, workspaceId: scope?.workspaceId ?? "" },
        )
        .then((timeline) => {
          if (!abort.signal.aborted && timeline.run_id === requested.runId)
            setLatestMetadata({ authority, latest: timeline.latest_available });
        })
        .catch((error) => {
          if (abort.signal.aborted) return;
          if (error instanceof ApiError && (error.code === 401 || error.code === 403)) revoke();
          else dirty = true;
        })
        .finally(() => {
          watermarkBusy = false;
        });
    }, 250);
    return () => {
      abort.abort();
      stop();
      clearInterval(timer);
    };
    // scopeRevision plus confirmed user and run fence every callback, including layout cleanup.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authority, subscriptionRetry, denied]);
  return {
    playbackRun: denied ? null : ((visible?.view ?? confirmedView)?.run ?? null),
    seekTime,
    seekEvent,
    seeking,
    layout,
    setLayout,
    selection,
    issues: parsed.issues,
    view: seeking || denied ? null : (visible?.view ?? confirmedView),
    detail: seeking || denied ? null : (visible?.detail ?? null),
    trace: seeking || denied ? null : (visible?.trace ?? null),
    loadState:
      (denied ? "forbidden" : undefined) ??
      (seeking ? seekLoadState : undefined) ??
      visible?.loadState ??
      ((confirmedView
        ? "refreshing"
        : scopeKey && requested.runId
          ? "loading"
          : "idle") as WorkbenchLoadState),
    error: seeking ? seekIntent.error : (visible?.error ?? null),
    latestAvailable: denied
      ? null
      : requested.at !== null && latestMetadata?.authority === authority
        ? latestMetadata.latest
        : ((visible?.view ?? confirmedView)?.run.latest_available ?? null),
    targetUnavailable:
      (visible?.targetUnavailable ?? false) ||
      (Boolean(scopeKey) &&
        missingNotice?.boundaryKey === boundaryKey &&
        missingNotice.selection === serializeSelection(requested) &&
        visible?.loadState !== "forbidden"),
    revokeContent: () => {
      generation.current += 1;
      controller.current?.abort();
      seekGeneration.current += 1;
      seekController.current?.abort();
      if (seekTimer.current) clearTimeout(seekTimer.current);
      setSnapshot(null);
      setLatestMetadata(null);
      setDeniedAuthority(authority);
    },
    dismissTargetNotice: () => {
      setMissingNotice(null);
      setSnapshot((previous) => (previous ? { ...previous, targetUnavailable: false } : null));
    },
    setSelection,
    returnToLive,
    refresh,
  };
}
