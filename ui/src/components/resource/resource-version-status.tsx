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

type ResourceVersionStatusApi<TVersionsData> = {
  listVersions: (resourceId: string) => Promise<TVersionsData>;
  createBuild: (resourceId: string) => Promise<unknown>;
  retryBuild: (resourceId: string, buildId: string) => Promise<unknown>;
  cancelBuild: (resourceId: string, buildId: string) => Promise<unknown>;
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
  /** When provided, history is controlled by the caller. */
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

type KnowledgeVersionMessages = SharedVersionMessages & {
  viewBuild: string;
  reindex: string;
};

function knowledgeVersionMessages(
  t: ReturnType<typeof useTranslations<"knowledge">>,
): KnowledgeVersionMessages {
  return {
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

function ResourceVersionStatusCore<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionRecordLike<TBuild>,
  TBuild extends BuildWithProgressInfo,
>({
  api,
  resourceId,
  controlledHistory,
  onBuildChanged,
  messages,
}: ResourceVersionStatusProps<TVersionsData> & {
  messages: KnowledgeVersionMessages;
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
      />
      <HistoricalVersions
        historical={historical}
        viewingVersionId={viewingVersionId}
        onView={setViewingVersionId}
        messages={messages}
        extraActions={
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={acting || Boolean(history?.active_build)}
            onClick={createBuild}
          >
            {history?.active_build ? messages.viewBuild : messages.reindex}
          </Button>
        }
      />
    </div>
  );
}

export function ResourceVersionStatus<
  TVersionsData extends VersionsDataLike<TVersion, TBuild>,
  TVersion extends VersionRecordLike<TBuild>,
  TBuild extends BuildWithProgressInfo,
>(props: ResourceVersionStatusProps<TVersionsData>) {
  const t = useTranslations("knowledge");
  return <ResourceVersionStatusCore {...props} messages={knowledgeVersionMessages(t)} />;
}
