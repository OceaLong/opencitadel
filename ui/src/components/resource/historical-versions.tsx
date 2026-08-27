"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

type VersionShape = {
  id: string;
};

export type HistoricalVersionMessages = {
  previousVersions: string;
  viewHistoricalVersion: (version: string) => string;
  viewingHistoricalVersion: (version: string) => string;
};

export type HistoricalVersionsProps<TVersion extends VersionShape> = {
  historical: TVersion[];
  viewingVersionId: string | null;
  onView: (versionId: string) => void;
  messages: HistoricalVersionMessages;
  /**
   * Resource-specific actions rendered after the historical version list
   * (shown regardless of whether there is any history). Knowledge bases use
   * this for their "create build"/reindex button; codebases pass nothing.
   */
  extraActions?: ReactNode;
};

/**
 * Shared "previous versions" panel behind the codebase and knowledge base
 * version-status widgets. Identical markup/copy across both resources
 * except for the `extraActions` slot.
 */
export function HistoricalVersions<TVersion extends VersionShape>({
  historical,
  viewingVersionId,
  onView,
  messages,
  extraActions,
}: HistoricalVersionsProps<TVersion>) {
  return (
    <>
      {!!historical.length && (
        <div className="space-y-1">
          <p className="text-muted-foreground">{messages.previousVersions}</p>
          <div className="flex flex-wrap gap-1">
            {historical.map((version) => (
              <Button
                key={version.id}
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => onView(version.id)}
              >
                {messages.viewHistoricalVersion(version.id)}
              </Button>
            ))}
          </div>
          {viewingVersionId && <p>{messages.viewingHistoricalVersion(viewingVersionId)}</p>}
        </div>
      )}
      {extraActions}
    </>
  );
}
