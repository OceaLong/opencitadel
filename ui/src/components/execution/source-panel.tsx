"use client";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

import type { StepView } from "@/lib/api/types/execution-view";
import { sourceTarget } from "@/lib/execution-view/source-target";

export type Citation = NonNullable<StepView["citation_refs"]>[number];
export type SourceLocator = "original" | "page" | "document";
export type ProducerSelection = { runId: string; stepId: string };

/** Presentation only: the caller supplies U05's single authorized body owner. */
export function SourcePanel({
  citation,
  body,
  title,
  locator = "original",
  onLocatorChange,
  producers = [],
  onSelectProducer,
}: {
  citation: Citation;
  body?: ReactNode;
  title?: string | null;
  locator?: SourceLocator;
  onLocatorChange?: (locator: SourceLocator) => void;
  producers?: ProducerSelection[];
  onSelectProducer?: (producer: ProducerSelection) => void;
}) {
  const t = useTranslations("executionSources");
  const target = sourceTarget(citation);
  const file = citation.resource_kind === "file";
  const fixed = file
    ? Boolean(citation.file_id)
    : Boolean(target.versionId && target.revisionId && target.docId && citation.knowledge_base_id);
  return (
    <section className="space-y-3" data-execution-source>
      <h3 className="font-medium break-all">{title ?? t("titleUnavailable")}</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs break-all">
        <dt>{t("citation")}</dt>
        <dd>{citation.citation_id}</dd>
        {file ? (
          <>
            <dt>{t("file")}</dt>
            <dd>{citation.file_id ?? t("unavailable")}</dd>
          </>
        ) : (
          <>
            <dt>{t("version")}</dt>
            <dd>{target.versionId ?? t("unavailable")}</dd>
            <dt>{t("revision")}</dt>
            <dd>{target.revisionId ?? t("unavailable")}</dd>
            <dt>{t("document")}</dt>
            <dd>{target.docId ?? t("unavailable")}</dd>
            <dt>{t("chunk")}</dt>
            <dd>{target.chunkId ?? t("notRecorded")}</dd>
            <dt>{t("page")}</dt>
            <dd>{target.page ?? t("notRecorded")}</dd>
          </>
        )}
      </dl>
      {citation.availability !== "available" ? (
        <p role="status">{t("unavailable")}</p>
      ) : (
        <>
          {!fixed && <p role="status">{t("metadataUnavailable")}</p>}
          {!file && onLocatorChange && (
            <div className="space-y-2">
              <p role="status" className="text-muted-foreground text-xs">
                {locator === "original"
                  ? t("originalLocator")
                  : locator === "page"
                    ? t("pageFallback")
                    : t("documentFallback")}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => onLocatorChange("original")}>
                  {t("original")}
                </Button>
                {target.page !== null && (
                  <Button
                    data-source-fallback="page"
                    variant="outline"
                    size="sm"
                    onClick={() => onLocatorChange("page")}
                  >
                    {t("openPage")}
                  </Button>
                )}
                <Button
                  data-source-fallback="document"
                  variant="outline"
                  size="sm"
                  onClick={() => onLocatorChange("document")}
                >
                  {t("openDocument")}
                </Button>
              </div>
            </div>
          )}
          {body}
        </>
      )}
      {producers.map((producer) => (
        <Button
          key={`${producer.runId}:${producer.stepId}`}
          variant="outline"
          size="sm"
          onClick={() => onSelectProducer?.(producer)}
        >
          {producer.runId} · {producer.stepId}
        </Button>
      ))}
    </section>
  );
}
