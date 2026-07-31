"use client";

import { useTranslations } from "next-intl";

import { BuildCandidatePanel } from "@/components/resource/build-candidate-panel";
import { HistoricalVersions } from "@/components/resource/historical-versions";
import { Button } from "@/components/ui/button";

import { useVersionBuildPolling } from "@/hooks/use-version-build-polling";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeBuild, KnowledgeVersion, KnowledgeVersionsData } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";

type KnowledgeVersionStatusProps = {
  knowledgeBaseId: string;
  onBuildChanged?: () => void;
};

export function KnowledgeVersionStatus({
  knowledgeBaseId,
  onBuildChanged,
}: KnowledgeVersionStatusProps) {
  const t = useTranslations("knowledge");

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
  } = useVersionBuildPolling<KnowledgeVersionsData, KnowledgeVersion, KnowledgeBuild>({
    api: knowledgeApi,
    resourceId: knowledgeBaseId,
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
    void runAction(() => knowledgeApi.createBuild(knowledgeBaseId));
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
      />
      <HistoricalVersions
        historical={historical}
        viewingVersionId={viewingVersionId}
        onView={setViewingVersionId}
        t={t}
        extraActions={
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={acting || Boolean(history?.active_build)}
            onClick={createBuild}
          >
            {history?.active_build ? t("viewBuild") : t("reindex")}
          </Button>
        }
      />
    </div>
  );
}
