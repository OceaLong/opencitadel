"use client";
import { useTranslations } from "next-intl";

import { MarkdownContent } from "@/components/markdown-content";
import { Button } from "@/components/ui/button";

import type { RunView, StepView } from "@/lib/api/types/execution-view";
import type { WorkbenchSelection } from "@/lib/execution-view/state";
/** Public summary slots shared by session and non-session routes. Detailed readers attach here. */
export function ExecutionSummary({
  run,
  step,
  selection,
}: {
  run: RunView | null;
  step: StepView | null;
  selection: WorkbenchSelection;
}) {
  const t = useTranslations("executionWorkbench");
  return (
    <div className="space-y-3 p-3 text-sm">
      <h2 className="font-medium break-all">
        {selection.artifactId ?? step?.step_id ?? run?.run_id ?? t("detail")}
      </h2>
      {selection.version !== null && <p>{t("version", { version: selection.version })}</p>}
      {(step?.public_summary ?? run?.public_summary) && (
        <MarkdownContent content={step?.public_summary ?? run?.public_summary ?? ""} />
      )}
      <dl className="space-y-2 text-xs">
        {step && (
          <>
            <dt>{t("statusLabel")}</dt>
            <dd>{t(`status.${step.status}`)}</dd>
            <dt>{t("duration")}</dt>
            <dd>
              {step.duration_ms == null
                ? t("unknownTiming")
                : t("milliseconds", { value: step.duration_ms })}
            </dd>
          </>
        )}
        {run?.as_of && (
          <>
            <dt>{t("updated")}</dt>
            <dd>{run.as_of}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
export function ExecutionSteps({
  steps,
  onSelectStep,
}: {
  steps: StepView[];
  onSelectStep: (id: string) => void;
}) {
  const t = useTranslations("executionWorkbench");
  return (
    <section className="space-y-3 p-3">
      <h2>{t("observedActivities")}</h2>
      <ol className="space-y-2">
        {steps.map((step) => (
          <li key={step.step_id}>
            <Button
              variant="ghost"
              className="h-auto min-h-12 w-full justify-between gap-3 text-left whitespace-normal"
              onClick={() => onSelectStep(step.step_id)}
            >
              <span className="min-w-0 break-all">
                {step.public_summary ?? step.tool_name ?? step.step_id}
              </span>
              <span className="shrink-0 text-xs">
                {t(`status.${step.status}`)} ·{" "}
                {step.duration_ms == null
                  ? t("unknownTiming")
                  : t("milliseconds", { value: step.duration_ms })}
              </span>
            </Button>
          </li>
        ))}
      </ol>
      {steps.length === 0 && <p>{t("noActivities")}</p>}
    </section>
  );
}
