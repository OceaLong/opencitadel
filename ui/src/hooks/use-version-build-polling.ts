"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const ACTIVE_BUILD_STATUSES = new Set(["new", "queued", "running", "waiting"]);
const DEFAULT_POLL_MS = 5000;

/** Minimal shape a resource build must have for shared polling/action logic. */
export type VersionBuildLike = {
  id: string;
  status: string;
  can_retry: boolean;
  can_cancel: boolean;
};

/** Minimal shape a resource version must have for shared derivation logic. */
export type VersionRecordLike<TBuild> = {
  id: string;
  is_active: boolean;
  is_published: boolean;
  capabilities: Record<string, unknown>;
  build?: TBuild | null;
};

/** Minimal shape the "list versions" response must have. */
export type VersionsDataLike<TVersion, TBuild> = {
  active_build?: TBuild | null;
  versions: TVersion[];
};

type VersionBuildApi<TVersionsData> = {
  listVersions: (resourceId: string) => Promise<TVersionsData>;
  retryBuild: (resourceId: string, buildId: string) => Promise<unknown>;
  cancelBuild: (resourceId: string, buildId: string) => Promise<unknown>;
};

/**
 * A build action to run. `"retry"`/`"cancel"` operate on the currently
 * actionable build via the injected api; a function runs an arbitrary
 * resource-specific mutation (e.g. knowledge base's "create build") through
 * the same acting/reload/error-handling machinery.
 */
export type VersionBuildAction = "retry" | "cancel" | (() => Promise<unknown>);

export type UseVersionBuildPollingOptions<TVersionsData> = {
  api: VersionBuildApi<TVersionsData>;
  resourceId: string;
  /**
   * When provided (even `null`), the hook treats history as controlled by
   * the caller: it never self-fetches, and on each poll tick it calls
   * `onBuildChanged` instead of reloading. Leave `undefined` for the
   * uncontrolled/self-fetching mode.
   */
  controlledHistory?: TVersionsData | null;
  onBuildChanged?: () => void;
  pollMs?: number;
  capabilityAvailableLabel: string;
  capabilityUnavailableLabel: string;
  loadErrorMessage: string;
  actionErrorMessage: string;
};

/**
 * Shared load + 5s poll + retry/cancel logic behind the codebase and
 * knowledge base "version status" widgets. Callers own everything
 * resource-specific (endpoints, extra actions, extra derived info); this
 * hook only knows about the generic version/build shape.
 */
export function useVersionBuildPolling<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionRecordLike<TBuild>,
  TBuild extends VersionBuildLike,
>({
  api,
  resourceId,
  controlledHistory,
  onBuildChanged,
  pollMs = DEFAULT_POLL_MS,
  capabilityAvailableLabel,
  capabilityUnavailableLabel,
  loadErrorMessage,
  actionErrorMessage,
}: UseVersionBuildPollingOptions<TVersionsData>) {
  const [loadedHistory, setLoadedHistory] = useState<TVersionsData | null>(null);
  const [loading, setLoading] = useState(controlledHistory === undefined);
  const [acting, setActing] = useState(false);
  const [viewingVersionId, setViewingVersionId] = useState<string | null>(null);
  const history = controlledHistory === undefined ? loadedHistory : controlledHistory;

  const load = useCallback(
    async (background = false) => {
      if (controlledHistory !== undefined) return;
      if (!background) setLoading(true);
      try {
        setLoadedHistory(await api.listVersions(resourceId));
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : loadErrorMessage);
      } finally {
        setLoading(false);
      }
    },
    [api, resourceId, controlledHistory, loadErrorMessage],
  );

  useEffect(() => {
    if (controlledHistory !== undefined) {
      setLoading(false);
      return;
    }
    void load();
  }, [controlledHistory, load]);

  const activeBuild = history?.active_build;
  useEffect(() => {
    if (!activeBuild || !ACTIVE_BUILD_STATUSES.has(activeBuild.status)) return;
    const timer = window.setInterval(() => {
      if (controlledHistory === undefined) {
        void load(true);
      } else {
        onBuildChanged?.();
      }
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [activeBuild, controlledHistory, load, onBuildChanged, pollMs]);

  const active = history?.versions.find((version) => version.is_active) ?? null;
  const historical =
    history?.versions.filter((version) => version.is_published && !version.is_active) ?? [];
  const viewed =
    history?.versions.find((version) => version.id === viewingVersionId) ?? active ?? null;
  const capabilities = viewed
    ? Object.entries(viewed.capabilities).map(
        ([name, enabled]) =>
          `${name}: ${enabled ? capabilityAvailableLabel : capabilityUnavailableLabel}`,
      )
    : [];
  const actionableBuild =
    history?.active_build ??
    history?.versions.find((version) => version.build?.can_retry)?.build ??
    null;

  const reload = useCallback(async () => {
    if (controlledHistory === undefined) {
      await load();
    }
    onBuildChanged?.();
  }, [controlledHistory, load, onBuildChanged]);

  const runAction = useCallback(
    async (action: VersionBuildAction) => {
      if (acting) return;
      const build = actionableBuild;
      const needsBuild = action === "retry" || action === "cancel";
      if (needsBuild && !build) return;
      setActing(true);
      try {
        if (action === "retry" && build) {
          await api.retryBuild(resourceId, build.id);
        } else if (action === "cancel" && build) {
          await api.cancelBuild(resourceId, build.id);
        } else if (typeof action === "function") {
          await action();
        }
        await reload();
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : actionErrorMessage);
      } finally {
        setActing(false);
      }
    },
    [acting, actionableBuild, api, resourceId, reload, actionErrorMessage],
  );

  return {
    loading,
    acting,
    history,
    active,
    historical,
    viewed,
    capabilities,
    actionableBuild,
    viewingVersionId,
    setViewingVersionId,
    runAction,
    reload,
  };
}
