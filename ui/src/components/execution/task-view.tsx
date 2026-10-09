"use client";
import { type ReactNode, useState } from "react";
import { useTranslations } from "next-intl";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";

import type { RunView, StepView, ViewPage } from "@/lib/api/types/execution-view";

export function stageProgress(completed: number, total: number | null): number | null {
  return total !== null && total > 0 ? Math.min(100, Math.max(0, (completed / total) * 100)) : null;
}
export type TaskViewProps = {
  renderIdentity?: { scopeId: string; at: string; ready: boolean };
  run: RunView;
  steps: StepView[];
  approvals?: ViewPage["approvals"];
  artifacts?: ViewPage["artifacts"];
  selectedStepId?: string | null;
  onSelectStep: (id: string) => void;
  onSelectArtifact: (id: string, version: number) => void;
  onSelectCitation?: (citationId: string, stepId: string) => void;
  onOpenApproval: (id: string) => void;
  canApprove?: boolean;
  approvalEntry?: ReactNode;
  artifactEntry?: ReactNode;
  clarificationEntry?: ReactNode;
};
export function TaskView({
  run,
  renderIdentity,
  steps,
  approvals = [],
  artifacts = [],
  selectedStepId,
  onSelectStep,
  onSelectArtifact,
  onSelectCitation,
  onOpenApproval,
  canApprove = false,
  approvalEntry,
  artifactEntry,
  clarificationEntry,
}: TaskViewProps) {
  const [focusedStep, setFocusedStep] = useState<string | null>(null);
  const t = useTranslations("executionWorkbench");
  const phases = steps.filter((step) => step.kind === "phase");
  const waiting = steps.filter(
    (step) =>
      step.status === "running" ||
      step.status === "waiting" ||
      step.status === "queued" ||
      step.status === "deferred",
  );
  const asks = steps.filter((step) => step.kind === "clarification" && step.status === "waiting");
  const pending = approvals.filter(
    (approval) => !approval.decision && approval.status !== "completed",
  );
  const exceptions = steps.filter(
    (step) =>
      step.status === "failed" ||
      step.business_outcome === "failure" ||
      step.status === "unknown" ||
      step.status === "deferred",
  );
  const groups = new Map<string, typeof artifacts>();
  for (const artifact of artifacts)
    groups.set(artifact.artifact_id, [...(groups.get(artifact.artifact_id) ?? []), artifact]);
  const rows = (items: StepView[], focus = false) => (
    <ul
      className="space-y-2"
      onKeyDown={(event) => {
        if (
          !["ArrowUp", "ArrowDown"].includes(event.key) ||
          event.altKey ||
          event.ctrlKey ||
          event.metaKey
        )
          return;
        const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("li > button")];
        const index = buttons.indexOf(event.target as HTMLButtonElement);
        if (index < 0) return;
        event.preventDefault();
        buttons[
          Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))
        ]?.focus();
      }}
    >
      {items.map((step, index) => (
        <li key={step.step_id}>
          <Button
            type="button"
            data-step={step.step_id}
            variant="ghost"
            className="h-auto min-h-9 w-full justify-start text-left whitespace-normal"
            tabIndex={
              (
                items.some((item) => item.step_id === focusedStep)
                  ? focusedStep === step.step_id
                  : index === 0
              )
                ? 0
                : -1
            }
            onFocus={() => setFocusedStep(step.step_id)}
            aria-pressed={selectedStepId === step.step_id}
            onClick={() => onSelectStep(step.step_id)}
          >
            <span
              key={JSON.stringify([
                renderIdentity?.at,
                step.projection_revision,
                step.public_summary,
              ])}
              {...{ elementtiming: focus ? "execution-progress" : "execution-step" }}
              data-native-content={focus ? "progress" : "step"}
              data-public-run={step.run_id}
              data-public-step={step.step_id}
              data-public-revision={step.projection_revision}
              className="block min-w-0 flex-1 break-words"
            >
              {step.public_summary ?? step.tool_name ?? step.phase ?? step.step_id}
            </span>
            <StatusBadge>{t(`status.${step.status}`)}</StatusBadge>
          </Button>
        </li>
      ))}
    </ul>
  );
  const block = (title: string, children: ReactNode) => (
    <section className="bg-card shadow-card space-y-3 rounded-md border p-3 md:p-4">
      <h2 className="text-sm font-medium">{title}</h2>
      {children}
    </section>
  );
  return (
    <div
      data-testid="task-view"
      data-native-view="task"
      data-native-ready={renderIdentity?.ready ?? false}
      data-public-scope={renderIdentity?.scopeId}
      data-public-run={run.run_id}
      data-public-at={renderIdentity?.at}
      data-public-revision={run.projection_revision}
      className="space-y-3 p-3 text-sm md:p-4"
    >
      {block(
        t("goal"),
        <>
          <div className="flex flex-wrap items-start gap-2">
            <h1
              key={JSON.stringify([
                renderIdentity?.at,
                run.projection_revision,
                run.public_summary,
              ])}
              {...{ elementtiming: "execution-summary" }}
              data-native-content="summary"
              className="min-w-0 flex-1 font-medium break-words"
            >
              {run.public_summary ?? run.run_id}
            </h1>
            <StatusBadge
              variant={
                run.status === "failed"
                  ? "destructive"
                  : run.status === "completed"
                    ? "success"
                    : run.status === "waiting"
                      ? "warning"
                      : "secondary"
              }
            >
              {t(`status.${run.status}`)}
            </StatusBadge>
          </div>
          <dl className="text-muted-foreground grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            {run.admitted_at && (
              <div>
                <dt>{t("started")}</dt>
                <dd>{run.admitted_at}</dd>
              </div>
            )}
            {run.duration_ms != null && (
              <div>
                <dt>{t("duration")}</dt>
                <dd className="font-mono">{t("milliseconds", { value: run.duration_ms })}</dd>
              </div>
            )}
            {run.configuration?.model_revision && (
              <div>
                <dt>{t("model")}</dt>
                <dd className="break-all">{run.configuration.model_revision}</dd>
              </div>
            )}
            {run.as_of && (
              <div>
                <dt>{t("updated")}</dt>
                <dd>{run.as_of}</dd>
              </div>
            )}
          </dl>
        </>,
      )}
      {(waiting.length > 0 || run.wait_reason) &&
        block(
          t("currentFocus"),
          <>
            {run.wait_reason && <p>{run.wait_reason}</p>}
            {rows(waiting, true)}
          </>,
        )}
      {(pending.length > 0 || approvalEntry) &&
        block(
          t("pendingApprovals"),
          <>
            {pending.map((approval) => (
              <Button
                key={approval.approval_id}
                data-approval
                type="button"
                variant="outline"
                disabled={!canApprove}
                onClick={() => onOpenApproval(approval.approval_id)}
              >
                {approval.approval_kind ?? approval.approval_id}
              </Button>
            ))}
            {!canApprove && <p className="text-muted-foreground text-xs">{t("readOnlyReason")}</p>}
            {approvalEntry}
          </>,
        )}
      {(asks.length > 0 || clarificationEntry) &&
        block(
          t("pendingClarifications"),
          <>
            {rows(asks)}
            {clarificationEntry}
          </>,
        )}
      {block(
        phases.length ? t("recordedPhases") : t("observedActivities"),
        steps.length ? (
          rows(phases.length ? phases : steps)
        ) : (
          <p className="text-muted-foreground">{t("noActivities")}</p>
        ),
      )}
      {artifactEntry ??
        (groups.size > 0 &&
          block(
            t("artifacts"),
            <ul className="space-y-2">
              {[...groups].map(([id, versions]) => (
                <li key={id}>
                  <details>
                    <summary className="cursor-pointer break-all">
                      {id} · {t("versions", { count: versions.length })}
                    </summary>
                    {versions.map((artifact) => (
                      <Button
                        key={artifact.version}
                        variant="ghost"
                        onClick={() => onSelectArtifact(id, artifact.version)}
                      >
                        {t("version", { version: artifact.version })} ·{" "}
                        {t(`availability.${artifact.availability}`)}
                      </Button>
                    ))}
                  </details>
                </li>
              ))}
            </ul>,
          ))}
      {steps.some((step) => step.citation_refs?.length) &&
        block(
          t("sources"),
          <div className="flex flex-wrap gap-2">
            {steps.flatMap((step) =>
              (step.citation_refs ?? []).map((ref) => (
                <Button
                  key={`${step.step_id}:${ref.citation_id}`}
                  data-task-citation={ref.citation_id}
                  variant="outline"
                  size="sm"
                  onClick={() => onSelectCitation?.(ref.citation_id, step.step_id)}
                >
                  {ref.citation_id}
                </Button>
              )),
            )}
          </div>,
        )}
      {(exceptions.length > 0 || run.status === "failed") &&
        block(
          t("exceptions"),
          <>
            {run.status === "failed" && <p>{t("runFailure")}</p>}
            {exceptions.map((step) => (
              <div key={step.step_id}>
                <p>
                  {step.business_outcome === "failure"
                    ? t("businessFailure")
                    : step.status === "deferred"
                      ? t("deferred")
                      : step.status === "unknown"
                        ? t("unknownResult")
                        : t("stepFailure")}
                </p>
                {rows([step])}
              </div>
            ))}
          </>,
        )}
      {run.completeness?.state !== "complete" && (
        <p role="status" className="text-muted-foreground">
          {t("partialData")} {run.completeness?.missing_fields.join(", ")}
        </p>
      )}
    </div>
  );
}
