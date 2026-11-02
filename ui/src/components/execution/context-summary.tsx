"use client";

import { useTranslations } from "next-intl";

import type { StepView } from "@/lib/api/types/execution-view";

export function ContextSummary({ step }: { step: StepView }) {
  const t = useTranslations("executionContext");
  const value = step.context;
  if (!value) return null;
  const state =
    step.status === "failed"
      ? "unavailable"
      : (value.state ?? (value.stage === "prepare" ? "preparing" : "compacting"));
  const counts = [
    [t("selectedSources"), value.selected_sources],
    [t("originalSources"), value.original_sources],
    [t("inputEstimate"), value.estimated_input_units],
    [t("inputBudget"), value.input_budget],
    [t("outputBudget"), value.output_budget],
    [t("summaryChunks"), value.summary_chunks],
  ] as const;
  const states = {
    unavailable: t("state.unavailable"),
    preparing: t("state.preparing"),
    compacting: t("state.compacting"),
    ready: t("state.ready"),
    needs_compaction: t("state.needs_compaction"),
    compacted: t("state.compacted"),
  };
  return (
    <section aria-label={t("title")} className="space-y-2 rounded border p-3">
      <h3 className="font-medium">{t("title")}</h3>
      <p role="status">{states[state]}</p>
      {step.end_reason && <p className="text-muted-foreground text-xs">{step.end_reason}</p>}
      <dl className="grid grid-cols-2 gap-2 text-xs">
        {counts.map(([label, count]) =>
          count == null ? null : (
            <div key={label}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd>{count.toLocaleString()}</dd>
            </div>
          ),
        )}
      </dl>
      {(value.definition_digest || value.context_digest) && (
        <details className="text-xs">
          <summary className="cursor-pointer">{t("details")}</summary>
          <dl className="mt-2 space-y-2 break-all">
            {value.definition_version != null && (
              <div>
                <dt>{t("definitionVersion")}</dt>
                <dd>{value.definition_version}</dd>
              </div>
            )}
            {value.definition_digest && (
              <div>
                <dt>{t("definitionDigest")}</dt>
                <dd className="font-mono">{value.definition_digest}</dd>
              </div>
            )}
            {value.context_digest && (
              <div>
                <dt>{t("contextDigest")}</dt>
                <dd className="font-mono">{value.context_digest}</dd>
              </div>
            )}
          </dl>
        </details>
      )}
    </section>
  );
}
