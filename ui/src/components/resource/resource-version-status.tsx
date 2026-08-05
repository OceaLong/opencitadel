"use client";

import { useTranslations } from "next-intl";

import { BuildCandidatePanel } from "@/components/resource/build-candidate-panel";
import { HistoricalVersions } from "@/components/resource/historical-versions";
import { Button } from "@/components/ui/button";

import {
  useVersionBuildPolling,
  type VersionBuildLike,
  type VersionRecordLike,
  type VersionsDataLike,
} from "@/hooks/use-version-build-polling";
import { IconLoading } from "@/lib/icons";

type ResourceNamespace = "knowledge" | "codebase";

type ResourceVersionStatusApi<TVersionsData> = {
  listVersions: (resourceId: string) => Promise<TVersionsData>;
  createBuild: (resourceId: string) => Promise<unknown>;
  retryBuild: (resourceId: string, buildId: string) => Promise<unknown>;
  cancelBuild: (resourceId: string, buildId: string) => Promise<unknown>;
};

/** Version record shape needed by the `ns === "codebase"` extraInfo slot
 * (degraded-reasons/unsupported-views), on top of the generic shape the
 * shared polling hook already requires. */
type VersionWithDegradationInfo<TBuild> = VersionRecordLike<TBuild> & {
  degraded_reasons: string[];
  metrics: Record<string, unknown>;
};

/** Build shape `BuildCandidatePanel` needs (mirrors its private `BuildShape`,
 * not exported there) -- a superset of the polling hook's `VersionBuildLike`. */
type BuildWithProgressInfo = VersionBuildLike & {
  phase?: string | null;
  progress: number;
  degraded_reasons: unknown[];
  error_message?: string | null;
  heartbeat_at?: string | null;
};

export type ResourceVersionStatusProps<TVersionsData> = {
  api: ResourceVersionStatusApi<TVersionsData>;
  resourceId: string;
  /**
   * Which resource this is, for both the i18n namespace (`useTranslations(ns)`)
   * and to pick the one namespace-specific slot below -- knowledge base and
   * codebase version-status widgets are otherwise pixel-identical.
   */
  ns: ResourceNamespace;
  /**
   * When provided (even `null`), history is controlled by the caller instead
   * of self-fetched -- mirrors `useVersionBuildPolling`'s `controlledHistory`.
   * Only the codebase library passes this today; the knowledge library lets
   * each card self-fetch.
   */
  controlledHistory?: TVersionsData | null;
  onBuildChanged?: () => void;
};

function unsupportedViews(version: { metrics?: unknown } | null): string[] {
  const raw = (version as { metrics?: Record<string, unknown> } | null)?.metrics
    ?.unsupported_views;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>).map(
    ([name, reason]) => `${name}: ${String(reason)}`,
  );
}

export function ResourceVersionStatus<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionWithDegradationInfo<TBuild>,
  TBuild extends BuildWithProgressInfo,
>({
  api,
  resourceId,
  ns,
  controlledHistory,
  onBuildChanged,
}: ResourceVersionStatusProps<TVersionsData>) {
  const t = useTranslations(ns);

  const {
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
  } = useVersionBuildPolling<TVersionsData, TVersion, TBuild>({
    api,
    resourceId,
    controlledHistory,
    onBuildChanged,
    t,
    loadErrorMessage: t("versionStatusLoadError"),
    actionErrorMessage: t("versionActionError"),
  });

  if (loading && !history) {
    return (
      <span role="status" aria-label={t("versionStatusLoading")}>
        <IconLoading className="size-3 animate-spin" />
      </span>
    );
  }

  const createBuild = () => {
    if (history?.active_build || acting) return;
    void runAction(() => api.createBuild(resourceId));
  };

  return (
    <div className="space-y-1 text-xs">
      <BuildCandidatePanel
        active={active}
        viewed={viewed}
        capabilities={capabilities}
        actionableBuild={actionableBuild}
        acting={acting}
        onRetry={() => void runAction("retry")}
        onCancel={() => void runAction("cancel")}
        t={t}
        extraInfo={
          ns === "codebase" ? (
            <>
              {!!viewed?.degraded_reasons.length && (
                <p className="text-amber-600 dark:text-amber-500">
                  {t("versionDegraded", {
                    reasons: viewed.degraded_reasons.join(", "),
                  })}
                </p>
              )}
              {!!unsupportedViews(viewed).length && (
                <p className="text-muted-foreground">
                  {t("unsupportedViews", { views: unsupportedViews(viewed).join(", ") })}
                </p>
              )}
            </>
          ) : undefined
        }
      />
      <HistoricalVersions
        historical={historical}
        viewingVersionId={viewingVersionId}
        onView={setViewingVersionId}
        t={t}
        extraActions={
          ns === "knowledge" ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={acting || Boolean(history?.active_build)}
              onClick={createBuild}
            >
              {history?.active_build ? t("viewBuild") : t("reindex")}
            </Button>
          ) : undefined
        }
      />
    </div>
  );
}
