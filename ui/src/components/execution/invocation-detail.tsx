"use client";
import { useTranslations } from "next-intl";

import { MarkdownContent } from "@/components/markdown-content";
import { SafeArtifactPreview } from "@/components/session/safe-artifact-preview";
import { PublicContentPreview } from "@/components/session/tool-preview-renderers";
import { Button } from "@/components/ui/button";

import type { DetailBodyState } from "@/hooks/use-execution-detail-body";
import type { StepView } from "@/lib/api/types/execution-view";
export function InvocationDetail({ detail }: { detail: StepView }) {
  const t = useTranslations("executionDetail");
  const statuses = {
    new: t("statusValue.new"),
    queued: t("statusValue.queued"),
    running: t("statusValue.running"),
    waiting: t("statusValue.waiting"),
    completed: t("statusValue.completed"),
    failed: t("statusValue.failed"),
    cancelled: t("statusValue.cancelled"),
    deferred: t("statusValue.deferred"),
    unknown: t("statusValue.unknown"),
  };
  const outcomes = {
    success: t("outcomeValue.success"),
    failure: t("outcomeValue.failure"),
    unknown: t("outcomeValue.unknown"),
  };
  return (
    <section className="space-y-2 text-sm">
      <h3 className="font-medium break-all">
        {detail.tool_name ?? detail.invocation_id ?? detail.step_id}
      </h3>
      {detail.public_summary && <MarkdownContent content={detail.public_summary} />}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt>{t("status")}</dt>
        <dd
          key={JSON.stringify([detail.projection_revision, detail.status])}
          {...{ elementtiming: "execution-detail-status" }}
          data-native-content="detail-status"
        >
          {statuses[detail.status]}
        </dd>
        <dt>{t("attempt")}</dt>
        <dd className="font-mono break-all">{detail.attempt_id ?? t("notRecorded")}</dd>
        <dt>{t("duration")}</dt>
        <dd className="font-mono">
          {detail.duration_ms == null
            ? t("notRecorded")
            : t("milliseconds", { value: detail.duration_ms })}
        </dd>
        {detail.business_outcome && (
          <>
            <dt>{t("outcome")}</dt>
            <dd>{outcomes[detail.business_outcome]}</dd>
          </>
        )}
        {detail.relationship === "unknown" &&
          detail.completeness.missing_fields.includes("parent_step_id") && (
            <>
              <dt>{t("parentRelationship")}</dt>
              <dd>{t("unknownParent")}</dd>
            </>
          )}
      </dl>
      {(detail.status === "unknown" || detail.business_outcome === "unknown") && (
        <p role="status" className="text-muted-foreground">
          {t("unknownOutcome")}
        </p>
      )}
      {detail.end_reason && (
        <div>
          <h4 className="font-medium">{t("endReason")}</h4>
          <p className="break-words">{detail.end_reason}</p>
        </div>
      )}
    </section>
  );
}
export function ContentSection({
  state,
  kind,
  onLoad,
  onDownload,
}: {
  state?: DetailBodyState;
  kind: "Input" | "Output" | "Artifact" | "Source";
  onLoad: () => void;
  onDownload: () => void;
}) {
  const t = useTranslations("executionDetail");
  const labels = {
    Input: { kind: t("kindInput"), load: t("loadInput"), download: t("downloadInput") },
    Output: { kind: t("kindOutput"), load: t("loadOutput"), download: t("downloadOutput") },
    Artifact: { kind: t("kindArtifact"), load: t("loadArtifact"), download: t("downloadArtifact") },
    Source: { kind: t("kindSource"), load: t("loadSource"), download: t("downloadSource") },
  };
  const pages = state?.pages ?? [],
    last = pages.at(-1);
  return (
    <section className="space-y-2 border-t pt-3">
      <h3 className="text-sm font-medium">{labels[kind].kind}</h3>
      <div className="flex flex-wrap gap-2">
        {(!last || last.next_cursor) && (
          <Button size="sm" variant="outline" disabled={state?.loading} onClick={onLoad}>
            {last ? t("nextPage") : labels[kind].load}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={state?.downloading} onClick={onDownload}>
          {labels[kind].download}
        </Button>
      </div>
      {state?.locatorUnavailable && <p role="status">{t("locatorUnavailable")}</p>}
      {state?.error && <p role="alert">{t("readError")}</p>}
      {pages.some((page) => page.redacted) && (
        <p className="text-muted-foreground text-xs">{t("redacted")}</p>
      )}
      {last?.truncated && (
        <p role="status" className="text-muted-foreground text-xs">
          {t("truncated")}
        </p>
      )}
      {pages.length > 0 &&
        (last?.content_type.includes("html") ? (
          <>
            <SafeArtifactPreview
              notice={t("safePreview")}
              title={labels[kind].kind}
              content={pages.map((page) => page.content ?? "").join("")}
            />
          </>
        ) : last?.content_type.includes("markdown") ? (
          <MarkdownContent content={pages.map((page) => page.content ?? "").join("")} />
        ) : (
          <PublicContentPreview
            content={pages.map((page) => page.content ?? "").join("")}
            contentType={last?.content_type ?? "text/plain"}
            truncated={Boolean(last?.truncated)}
          />
        ))}
    </section>
  );
}
