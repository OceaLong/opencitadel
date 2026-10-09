"use client";
import { type ReactNode, useLayoutEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { ArtifactWorkbench } from "@/components/session/artifact-workbench";
import { Button } from "@/components/ui/button";

import type { DetailBodyOwner } from "@/hooks/use-execution-detail-body";
import { artifactsApi } from "@/lib/api/artifacts";
import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { ArtifactEventSummary } from "@/lib/api/types";
import type { ArtifactProvenance, ViewPage } from "@/lib/api/types/execution-view";

import type { Citation, ProducerSelection } from "./source-panel";

type Props = {
  runId: string;
  at: string;
  owner: DetailBodyOwner;
  artifacts: ViewPage["artifacts"];
  onSelectProducer: (producer: ProducerSelection) => void;
  onSelectCitation: (citation: Citation) => void;
  onRevoked: () => void;
  onSelectArtifact?: (id: string, version: number) => void;
  selectedArtifactId?: string | null;
  version?: number | null;
  body?: ReactNode;
  sessionId?: string;
};
type Entry = {
  ref: NonNullable<ViewPage["artifacts"]>[number];
  metadata: ArtifactEventSummary | null;
  provenance: ArtifactProvenance[];
};
export function ArtifactPanel(props: Props) {
  return (
    <BoundArtifactPanel
      key={JSON.stringify([props.owner.key, props.runId, props.at, props.artifacts])}
      {...props}
    />
  );
}
function BoundArtifactPanel({
  runId,
  at,
  owner,
  artifacts = [],
  onSelectProducer,
  onSelectCitation,
  onRevoked,
  onSelectArtifact,
  selectedArtifactId,
  version,
  body,
  sessionId,
}: Props) {
  const t = useTranslations("executionArtifacts");
  const evidenceLabels = { direct: t("direct"), derived: t("derived"), unknown: t("unknown") };
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [error, setError] = useState(false);
  useLayoutEffect(() => {
    const controller = new AbortController();
    const options = {
      workspaceId: owner.workspaceId,
      signal: controller.signal,
      skipErrorHandler: true,
    };
    void (async () => {
      try {
        const result: Entry[] = [];
        for (const ref of artifacts) {
          if (controller.signal.aborted) return;
          const provenance = await executionViewApi.getProvenance(
            ref.artifact_id,
            ref.version,
            options,
            { run_id: runId, at },
          );
          if (controller.signal.aborted) return;
          const metadata = await artifactsApi.get(ref.artifact_id, options);
          result.push({
            ref,
            provenance,
            metadata: {
              artifact_id: ref.artifact_id,
              version: ref.version,
              title: metadata.title,
              kind: metadata.kind,
              status: metadata.status,
              storage_ref: "",
            },
          });
        }
        if (!controller.signal.aborted) setEntries(result);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setEntries(null);
        setError(true);
        if (reason instanceof ApiError && [401, 403, 404].includes(reason.code)) onRevoked();
      }
    })();
    return () => controller.abort();
    // Bound component identity includes the whole authorized selection and refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner.key]);
  const ordered = [...(entries ?? [])].sort((a, b) => {
    const position = (entry: Entry) =>
      Math.min(
        ...entry.provenance
          .filter((p) => p.producer_run_id === runId && p.production_order != null)
          .map((p) => p.production_order!),
      );
    return position(a) - position(b);
  });
  const selected = ordered.find(
    (entry) => entry.ref.artifact_id === selectedArtifactId && entry.ref.version === version,
  );
  return (
    <section className="space-y-3" data-execution-artifacts>
      {!entries && <p role="status">{error ? t("unavailable") : t("loading")}</p>}
      {ordered.map((entry) => (
        <article
          key={`${entry.ref.artifact_id}:${entry.ref.version}`}
          className="space-y-2 rounded-md border p-3"
        >
          <Button
            variant="ghost"
            className="h-auto break-all whitespace-normal"
            onClick={() => onSelectArtifact?.(entry.ref.artifact_id, entry.ref.version)}
          >
            {entry.metadata?.title ?? entry.ref.artifact_id} ·{" "}
            {t("version", { version: entry.ref.version })}
          </Button>
          <p className="text-muted-foreground text-xs break-all">{entry.ref.artifact_id}</p>
          {entry.provenance.length === 0 && <p>{t("producerUnknown")}</p>}
          {entry.provenance.map((association, index) => (
            <div
              key={`${association.producer_run_id}:${association.produced_event_id}:${index}`}
              className="space-y-1 border-t pt-2"
            >
              <p className="text-muted-foreground text-xs">
                {evidenceLabels[association.evidence_kind]} ·{" "}
                {association.production_order == null
                  ? t("orderUnknown")
                  : t("order", { order: association.production_order })}
              </p>
              {association.binding_status !== "bound" ||
              !association.producer_run_id ||
              association.producer_step_ids.length === 0 ? (
                <p>{t("producerUnknown")}</p>
              ) : (
                association.producer_step_ids.map((step) => (
                  <Button
                    data-producer={`${association.producer_run_id}:${step}`}
                    key={step}
                    variant="outline"
                    size="sm"
                    className="h-auto break-all whitespace-normal"
                    onClick={() =>
                      onSelectProducer({ runId: association.producer_run_id!, stepId: step })
                    }
                  >
                    {association.producer_run_id} · {step}
                  </Button>
                ))
              )}
              {(association.citation_refs ?? []).map((citation) => (
                <Button
                  key={citation.citation_id}
                  data-artifact-citation={citation.citation_id}
                  variant="outline"
                  size="sm"
                  onClick={() => onSelectCitation(citation)}
                >
                  {t("source")} · {citation.citation_id}
                </Button>
              ))}
            </div>
          ))}
        </article>
      ))}
      {!selected && body !== undefined && body}
      {selected && body !== undefined && (
        <ArtifactWorkbench
          sessionId={sessionId ?? runId}
          artifacts={ordered
            .filter((entry) => entry.metadata)
            .map((entry) => entry.metadata!)
            .filter(
              (value, index, all) =>
                all.findIndex((other) => other.artifact_id === value.artifact_id) === index,
            )}
          focusedArtifactId={selected.ref.artifact_id}
          version={version}
          visibleVersions={ordered
            .filter((entry) => entry.ref.artifact_id === selected.ref.artifact_id)
            .map((entry) => entry.ref.version)}
          onVersionChange={(next) => onSelectArtifact?.(selected.ref.artifact_id, next)}
          onArtifactChange={(id) => {
            const next = ordered.find((entry) => entry.ref.artifact_id === id);
            if (next) onSelectArtifact?.(id, next.ref.version);
          }}
          body={body}
          className="min-h-72"
        />
      )}
    </section>
  );
}
