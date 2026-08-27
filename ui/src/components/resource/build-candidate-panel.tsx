"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

type VersionShape = {
  id: string;
};

export type BuildCandidateMessages = {
  activeVersion: (version: string) => string;
  noActiveVersion: string;
  versionCapabilities: (capabilities: string) => string;
  noCapabilities: string;
  candidateBuild: (values: { state: string; phase: string; progress: number }) => string;
  candidatePhasePending: string;
  retryBuild: string;
  cancelBuild: string;
};

type BuildShape = {
  id: string;
  status: string;
  phase?: string | null;
  progress: number;
  failure_code?: string | null;
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
  messages: BuildCandidateMessages;
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
  messages,
  extraInfo,
}: BuildCandidatePanelProps<TVersion, TBuild>) {
  return (
    <>
      <p>{active ? messages.activeVersion(active.id) : messages.noActiveVersion}</p>
      {viewed && (
        <>
          <p className="text-muted-foreground">
            {messages.versionCapabilities(
              capabilities.length ? capabilities.join(", ") : messages.noCapabilities,
            )}
          </p>
          {extraInfo}
        </>
      )}
      {actionableBuild && (
        <div className="text-muted-foreground">
          <p>
            {messages.candidateBuild({
              state: actionableBuild.status,
              phase: actionableBuild.phase ?? messages.candidatePhasePending,
              progress: actionableBuild.progress,
            })}
          </p>
          {actionableBuild.failure_code && (
            <p role="alert" className="text-destructive">
              {actionableBuild.failure_code}
            </p>
          )}
          <div className="mt-1 flex gap-1">
            {actionableBuild.can_retry && (
              <Button type="button" size="sm" variant="outline" disabled={acting} onClick={onRetry}>
                {messages.retryBuild}
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
                {messages.cancelBuild}
              </Button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
