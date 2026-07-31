"use client";

import type { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

type TFunction = ReturnType<typeof useTranslations>;

type VersionShape = {
  id: string;
};

type BuildShape = {
  id: string;
  state: string;
  phase?: string | null;
  progress: number;
  degraded_reasons: unknown[];
  error_message?: string | null;
  heartbeat_at?: string | null;
  can_retry: boolean;
  can_cancel: boolean;
};

export type BuildCandidatePanelProps<TVersion extends VersionShape, TBuild extends BuildShape> = {
  active: TVersion | null;
  viewed: TVersion | null;
  capabilities: string[];
  actionableBuild: TBuild | null;
  acting: boolean;
  onRetry: () => void;
  onCancel: () => void;
  t: TFunction;
  /**
   * Resource-specific extra info rendered right after the capabilities line
   * (only while a version is being viewed). Codebase uses this for its
   * degraded-reasons/unsupported-views blocks; knowledge bases pass nothing.
   */
  extraInfo?: ReactNode;
};

/**
 * Shared "current version + candidate build" panel behind the codebase and
 * knowledge base version-status widgets. Identical markup/copy across both
 * resources except for the `extraInfo` slot.
 */
export function BuildCandidatePanel<TVersion extends VersionShape, TBuild extends BuildShape>({
  active,
  viewed,
  capabilities,
  actionableBuild,
  acting,
  onRetry,
  onCancel,
  t,
  extraInfo,
}: BuildCandidatePanelProps<TVersion, TBuild>) {
  return (
    <>
      <p>{active ? t("activeVersion", { version: active.id }) : t("noActiveVersion")}</p>
      {viewed && (
        <>
          <p className="text-muted-foreground">
            {t("versionCapabilities", {
              capabilities: capabilities.length ? capabilities.join(", ") : t("noCapabilities"),
            })}
          </p>
          {extraInfo}
        </>
      )}
      {actionableBuild && (
        <div className="text-muted-foreground">
          <p>
            {t("candidateBuild", {
              state: actionableBuild.state,
              phase: actionableBuild.phase ?? t("candidatePhasePending"),
              progress: Math.round(actionableBuild.progress * 100),
            })}
          </p>
          {actionableBuild.error_message && (
            <p role="alert" className="text-destructive">
              {actionableBuild.error_message}
            </p>
          )}
          {!!actionableBuild.degraded_reasons.length && (
            <p className="text-amber-600 dark:text-amber-500">
              {t("candidateDegraded", {
                reasons: actionableBuild.degraded_reasons.join(", "),
              })}
            </p>
          )}
          {actionableBuild.heartbeat_at && (
            <p>
              {t("candidateHeartbeat", {
                heartbeat: actionableBuild.heartbeat_at,
              })}
            </p>
          )}
          <div className="mt-1 flex gap-1">
            {actionableBuild.can_retry && (
              <Button type="button" size="sm" variant="outline" disabled={acting} onClick={onRetry}>
                {t("retryBuild")}
              </Button>
            )}
            {actionableBuild.can_cancel && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={acting}
                onClick={onCancel}
              >
                {t("cancelBuild")}
              </Button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
