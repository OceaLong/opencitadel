"use client";
import { useTranslations } from "next-intl";

import { type DetailBodyTarget, useExecutionDetailBody } from "@/hooks/use-execution-detail-body";
import { analysisApi } from "@/lib/api/execution-analysis";
import type { StepView } from "@/lib/api/types/execution-view";

import { ContentSection, InvocationDetail } from "./invocation-detail";
export type RetainedDetailPanelProps = {
  ownerKey: string;
  workspaceId: string;
  comparisonId: string;
  revision: number;
  step: StepView;
  onRevoked: () => void;
};
export function RetainedDetailPanel({
  ownerKey,
  workspaceId,
  comparisonId,
  revision,
  step,
  onRevoked,
}: RetainedDetailPanelProps) {
  const t = useTranslations("executionDetail");
  // This owner key is a comparison identity, never passed to an execution `at` endpoint.
  const body = useExecutionDetailBody({ key: ownerKey, workspaceId, at: ownerKey }, onRevoked);
  const selections = [
    ...(step.input_ref
      ? [
          {
            kind: "input" as const,
            label: "Input" as const,
            available: step.input_ref.availability === "available",
          },
        ]
      : []),
    ...(step.output_ref
      ? [
          {
            kind: "output" as const,
            label: "Output" as const,
            available: step.output_ref.availability === "available",
          },
        ]
      : []),
    ...(step.artifact_refs ?? []).map((a) => ({
      kind: "artifact" as const,
      label: "Artifact" as const,
      artifact_id: a.artifact_id,
      version: a.version,
      available: a.availability === "available",
    })),
  ];
  return (
    <section className="min-w-0 space-y-3" data-retained-body-owner={step.run_id}>
      <InvocationDetail detail={step} />
      {selections.map((selection) => {
        const query = {
          revision,
          run_id: step.run_id,
          step_id: step.step_id,
          kind: selection.kind,
          ...("artifact_id" in selection
            ? { artifact_id: selection.artifact_id, version: selection.version }
            : {}),
        };
        const key = JSON.stringify(query);
        const target: DetailBodyTarget = {
          key,
          filename: `retained-${step.run_id}-${selection.kind}.txt`,
          localUnavailableReasons: ["retained_preview_limit", "binary_metadata_only"],
          read: (cursor, o) => analysisApi.body(comparisonId, { ...query, cursor }, o),
          download: async (o) => {
            const parts: string[] = [];
            let cursor: string | undefined;
            for (let i = 0; i < 32; i++) {
              const p = await analysisApi.body(comparisonId, { ...query, cursor }, o);
              if (p.availability !== "available" || p.content == null)
                throw new Error("retained_body_unavailable");
              parts.push(p.content);
              if (!p.next_cursor) return new Blob(parts, { type: p.content_type });
              cursor = p.next_cursor;
            }
            throw new Error("retained_body_limit");
          },
        };
        return selection.available ? (
          <div key={key}>
            <p className="text-xs break-all">
              {"artifact_id" in selection ? `${selection.artifact_id} v${selection.version}` : ""}
            </p>
            <ContentSection
              kind={selection.label}
              state={body.items[key]}
              onLoad={() => void body.load(target)}
              onDownload={() => void body.download(target)}
            />
          </div>
        ) : (
          <p key={key}>
            {selection.label}: {t("unavailable")}
          </p>
        );
      })}
    </section>
  );
}
