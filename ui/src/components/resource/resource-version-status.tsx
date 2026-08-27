"use client";

import { useTranslations } from "next-intl";

import {
  type BuildCandidateMessages,
  BuildCandidatePanel,
} from "@/components/resource/build-candidate-panel";
import {
  type HistoricalVersionMessages,
  HistoricalVersions,
} from "@/components/resource/historical-versions";
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
  failure_code?: string | null;
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

type SharedVersionMessages = BuildCandidateMessages &
  HistoricalVersionMessages & {
    versionStatusLoadError: string;
    versionActionError: string;
    versionStatusLoading: string;
    capabilityAvailable: string;
    capabilityUnavailable: string;
  };

type CodebaseVersionMessages = SharedVersionMessages & {
  kind: "codebase";
  versionDegraded: (reasons: string) => string;
  unsupportedViews: (views: string) => string;
};

type KnowledgeVersionMessages = SharedVersionMessages & {
  kind: "knowledge";
  viewBuild: string;
  reindex: string;
};

type ResourceVersionMessages = CodebaseVersionMessages | KnowledgeVersionMessages;

function codebaseVersionMessages(
  t: ReturnType<typeof useTranslations<"codebase">>,
): CodebaseVersionMessages {
  return {
    kind: "codebase",
    activeVersion: (version) => t("activeVersion", { version }),
    noActiveVersion: t("noActiveVersion"),
    versionCapabilities: (capabilities) => t("versionCapabilities", { capabilities }),
    noCapabilities: t("noCapabilities"),
    candidateBuild: (values) => t("candidateBuild", values),
    candidatePhasePending: t("candidatePhasePending"),
    retryBuild: t("retryBuild"),
    cancelBuild: t("cancelBuild"),
    previousVersions: t("previousVersions"),
    viewHistoricalVersion: (version) => t("viewHistoricalVersion", { version }),
    viewingHistoricalVersion: (version) => t("viewingHistoricalVersion", { version }),
    versionStatusLoadError: t("versionStatusLoadError"),
    versionActionError: t("versionActionError"),
    versionStatusLoading: t("versionStatusLoading"),
    capabilityAvailable: t("capabilityAvailable"),
    capabilityUnavailable: t("capabilityUnavailable"),
    versionDegraded: (reasons) => t("versionDegraded", { reasons }),
    unsupportedViews: (views) => t("unsupportedViews", { views }),
  };
}

function knowledgeVersionMessages(
  t: ReturnType<typeof useTranslations<"knowledge">>,
): KnowledgeVersionMessages {
  return {
    kind: "knowledge",
    activeVersion: (version) => t("activeVersion", { version }),
    noActiveVersion: t("noActiveVersion"),
    versionCapabilities: (capabilities) => t("versionCapabilities", { capabilities }),
    noCapabilities: t("noCapabilities"),
    candidateBuild: (values) => t("candidateBuild", values),
    candidatePhasePending: t("candidatePhasePending"),
    retryBuild: t("retryBuild"),
    cancelBuild: t("cancelBuild"),
    previousVersions: t("previousVersions"),
    viewHistoricalVersion: (version) => t("viewHistoricalVersion", { version }),
    viewingHistoricalVersion: (version) => t("viewingHistoricalVersion", { version }),
    versionStatusLoadError: t("versionStatusLoadError"),
    versionActionError: t("versionActionError"),
    versionStatusLoading: t("versionStatusLoading"),
    capabilityAvailable: t("capabilityAvailable"),
    capabilityUnavailable: t("capabilityUnavailable"),
    viewBuild: t("viewBuild"),
    reindex: t("reindex"),
  };
}

function unsupportedViews(version: { metrics?: unknown } | null): string[] {
  const raw = (version as { metrics?: Record<string, unknown> } | null)?.metrics?.unsupported_views;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>).map(
    ([name, reason]) => `${name}: ${String(reason)}`,
  );
}

function ResourceVersionStatusCore<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionWithDegradationInfo<TBuild>,
  TBuild extends BuildWithProgressInfo,
>({
  api,
  resourceId,
  controlledHistory,
  onBuildChanged,
  messages,
}: Omit<ResourceVersionStatusProps<TVersionsData>, "ns"> & {
  messages: ResourceVersionMessages;
}) {
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
    capabilityAvailableLabel: messages.capabilityAvailable,
    capabilityUnavailableLabel: messages.capabilityUnavailable,
    loadErrorMessage: messages.versionStatusLoadError,
    actionErrorMessage: messages.versionActionError,
  });

  if (loading && !history) {
    return (
      <span role="status" aria-label={messages.versionStatusLoading}>
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
        messages={messages}
        extraInfo={
          messages.kind === "codebase" ? (
            <>
              {!!viewed?.degraded_reasons.length && (
                <p className="text-warning">
                  {messages.versionDegraded(viewed.degraded_reasons.join(", "))}
                </p>
              )}
              {!!unsupportedViews(viewed).length && (
                <p className="text-muted-foreground">
                  {messages.unsupportedViews(unsupportedViews(viewed).join(", "))}
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
        messages={messages}
        extraActions={
          messages.kind === "knowledge" ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={acting || Boolean(history?.active_build)}
              onClick={createBuild}
            >
              {history?.active_build ? messages.viewBuild : messages.reindex}
            </Button>
          ) : undefined
        }
      />
    </div>
  );
}

function CodebaseResourceVersionStatus<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionWithDegradationInfo<TBuild>,
  TBuild extends BuildWithProgressInfo,
>(props: Omit<ResourceVersionStatusProps<TVersionsData>, "ns">) {
  const t = useTranslations("codebase");
  return <ResourceVersionStatusCore {...props} messages={codebaseVersionMessages(t)} />;
}

function KnowledgeResourceVersionStatus<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionWithDegradationInfo<TBuild>,
  TBuild extends BuildWithProgressInfo,
>(props: Omit<ResourceVersionStatusProps<TVersionsData>, "ns">) {
  const t = useTranslations("knowledge");
  return <ResourceVersionStatusCore {...props} messages={knowledgeVersionMessages(t)} />;
}

export function ResourceVersionStatus<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionWithDegradationInfo<TBuild>,
  TBuild extends BuildWithProgressInfo,
>({ ns, ...props }: ResourceVersionStatusProps<TVersionsData>) {
  return ns === "codebase" ? (
    <CodebaseResourceVersionStatus {...props} />
  ) : (
    <KnowledgeResourceVersionStatus {...props} />
  );
}
