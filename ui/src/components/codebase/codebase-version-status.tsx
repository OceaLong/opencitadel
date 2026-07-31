"use client";

import { useTranslations } from "next-intl";

import { BuildCandidatePanel } from "@/components/resource/build-candidate-panel";
import { HistoricalVersions } from "@/components/resource/historical-versions";

import { useVersionBuildPolling } from "@/hooks/use-version-build-polling";
import { codebaseApi } from "@/lib/api/codebase";
import type { CodebaseBuild, CodebaseVersion, CodebaseVersionsData } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";

type CodebaseVersionStatusProps = {
  codebaseId: string;
  history?: CodebaseVersionsData | null;
  onBuildChanged?: () => void;
};

function unsupportedViews(version: CodebaseVersion | null): string[] {
  const raw = version?.metrics?.unsupported_views;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>).map(
    ([name, reason]) => `${name}: ${String(reason)}`,
  );
}

export function CodebaseVersionStatus({
  codebaseId,
  history: controlledHistory,
  onBuildChanged,
}: CodebaseVersionStatusProps) {
  const t = useTranslations("codebase");

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
  } = useVersionBuildPolling<CodebaseVersionsData, CodebaseVersion, CodebaseBuild>({
    api: codebaseApi,
    resourceId: codebaseId,
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

  const unsupported = unsupportedViews(viewed);

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
          <>
            {!!viewed?.degraded_reasons.length && (
              <p className="text-amber-600 dark:text-amber-500">
                {t("versionDegraded", {
                  reasons: viewed.degraded_reasons.join(", "),
                })}
              </p>
            )}
            {!!unsupported.length && (
              <p className="text-muted-foreground">
                {t("unsupportedViews", { views: unsupported.join(", ") })}
              </p>
            )}
          </>
        }
      />
      <HistoricalVersions
        historical={historical}
        viewingVersionId={viewingVersionId}
        onView={setViewingVersionId}
        t={t}
      />
    </div>
  );
}
