"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

import { useCapabilities } from "@/hooks/use-capabilities";
import { hasExecutionGrant } from "@/lib/api/capabilities";
import { evaluationApi } from "@/lib/api/evaluations";
import { executionViewApi } from "@/lib/api/execution-view";
import type { CaseRevision, DatasetDraft, DatasetSummary } from "@/lib/api/types/evaluations";
import type { StepView } from "@/lib/api/types/execution-view";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Check, Choice, Field } from "./form-fields";
export function CaptureCaseAction({ runId, at }: { runId: string; at: string }) {
  const t = useTranslations("evaluations");
  const capabilities = useCapabilities();
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  if (
    loading ||
    !user ||
    !scope ||
    scope.userId !== user.id ||
    !hasExecutionGrant(capabilities.snapshot ?? undefined, "evaluation.manage")
  )
    return null;
  return (
    <CaptureDialog
      key={JSON.stringify([scope, scopeRevision, runId, at])}
      runId={runId}
      at={at}
      access={{
        workspaceId: scope.workspaceId,
        canManage: true,
        canRun: false,
        canRegister: false,
      }}
      title={t("captureCase")}
    />
  );
}
function CaptureDialog({
  runId,
  at,
  access,
  title,
}: {
  runId: string;
  at: string;
  access: EvaluationAccess;
  title: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" variant="outline">
          {title}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto p-3 max-sm:h-dvh max-sm:max-h-dvh max-sm:max-w-full max-sm:rounded-none sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{title}</DialogDescription>
        </DialogHeader>
        {open && <CaptureForm runId={runId} at={at} access={access} />}
      </DialogContent>
    </Dialog>
  );
}
function CaptureForm({
  runId,
  at,
  access,
}: {
  runId: string;
  at: string;
  access: EvaluationAccess;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [command, setCommand] = useState<"read" | "save" | null>(null);
  const [dataset, setDataset] = useState<DatasetDraft | null>(null);
  const [steps, setSteps] = useState<StepView[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [step, setStep] = useState("");
  const [key, setKey] = useState("");
  const [inspection, setInspection] = useState<{ identity: string; value: CaseRevision } | null>(
    null,
  );
  const identity = JSON.stringify([dataset?.id, dataset?.revision, runId, step, at, key]);
  const preview = inspection?.identity === identity ? inspection.value : null;
  const selectionLocked = task.pending && command !== "read";
  const dependentSelectionLocked = selectionLocked || (task.pending && !!datasetId && !dataset);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [knowledge, setKnowledge] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void runTask(
      async (o) => ({
        datasets: await evaluationApi.datasets(o),
        steps: await executionViewApi.listSteps(runId, { at, limit: 200 }, o),
      }),
      (v) => {
        setDatasets(v.datasets);
        setSteps(v.steps.items);
        setCursor(v.steps.next_cursor ?? null);
      },
    );
  }, [runTask, runId, at]);
  if (task.error === "denied") return <EvaluationError error="denied" />;
  return (
    <div className="space-y-3">
      <EvaluationError error={task.error} />
      <p className="text-sm">{t("captureExplain")}</p>
      <Choice
        label={t("dataset")}
        value={datasetId}
        disabled={selectionLocked}
        onChange={(id) => {
          if (selectionLocked) return;
          task.cancel();
          setCommand("read");
          setDatasetId(id);
          setDataset(null);
          setInspection(null);
          setSaved(false);
          if (id) void runTask((o) => evaluationApi.dataset(id, o), setDataset, true);
        }}
      >
        <option value="">{t("select")}</option>
        {datasets.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}
          </option>
        ))}
      </Choice>
      <Choice
        label={t("sourceStep")}
        value={step}
        disabled={dependentSelectionLocked}
        onChange={(v) => {
          if (dependentSelectionLocked) return;
          task.cancel();
          setStep(v);
          setInspection(null);
          setSaved(false);
        }}
      >
        <option value="">{t("select")}</option>
        {steps
          .filter((s) => s.activity_id && s.status === "completed" && s.kind === "model")
          .map((s) => (
            <option key={s.step_id} value={s.step_id}>
              {s.public_summary || s.step_id}
            </option>
          ))}
      </Choice>
      {cursor && (
        <Button
          variant="outline"
          disabled={task.pending}
          onClick={() =>
            void runTask(
              (o) => executionViewApi.listSteps(runId, { at, limit: 200, cursor }, o),
              (v) => {
                setSteps((old) => [...old, ...v.items]);
                setCursor(v.next_cursor ?? null);
              },
            )
          }
        >
          {t("more")}
        </Button>
      )}
      <Field
        label={t("caseKey")}
        value={key}
        disabled={dependentSelectionLocked}
        onChange={(v) => {
          if (dependentSelectionLocked) return;
          task.cancel();
          setKey(v);
          setInspection(null);
          setSaved(false);
        }}
        required
      />
      <Button
        disabled={!dataset || !step || !key.trim() || task.pending}
        onClick={() => {
          if (!dataset) return;
          setCommand("read");
          void runTask(
            (o) =>
              evaluationApi.previewFromRun(
                dataset.id,
                {
                  run_id: runId,
                  step_id: step,
                  at,
                  case_key: key,
                  expected_revision: dataset.revision,
                },
                o,
              ),
            (v) => {
              setInspection({ identity, value: v });
              setAttachments([]);
              setKnowledge([]);
            },
          );
        }}
      >
        {t("inspect")}
      </Button>
      {preview && (
        <section className="space-y-2">
          <p>{t(preview.input_status ?? "provided")}</p>
          <h3>{t("input")}</h3>
          <pre className="max-h-48 overflow-auto text-sm break-words whitespace-pre-wrap">
            {typeof preview.input === "string"
              ? preview.input
              : preview.input.map((m) => `${m.role}: ${m.content}`).join("\n")}
          </pre>
          <h3>{t("conversationHistory")}</h3>
          <ul className="max-h-48 overflow-auto text-sm">
            {preview.history?.map((m, i) => (
              <li key={i}>
                {m.role}: {m.content}
              </li>
            ))}
          </ul>
          <h3>{t("candidate")}</h3>
          <pre className="max-h-48 overflow-auto text-sm break-words whitespace-pre-wrap">
            {preview.reference_candidate ?? t("unavailable")}
          </pre>
          <p className="text-sm">{t("candidateUnconfirmed")}</p>
          <fieldset disabled={task.pending}>
            <legend>{t("resources")}</legend>
            {preview.attachments?.map((id) => (
              <Check
                key={id}
                label={id}
                checked={attachments.includes(id)}
                onChange={(checked) =>
                  setAttachments((old) => (checked ? [...old, id] : old.filter((v) => v !== id)))
                }
              />
            ))}
            {preview.knowledge_bindings?.map((b) => (
              <Check
                key={b.resource_id}
                label={`${b.resource_id} · ${b.version_id}`}
                checked={knowledge.includes(b.resource_id)}
                onChange={(checked) =>
                  setKnowledge((old) =>
                    checked ? [...old, b.resource_id] : old.filter((v) => v !== b.resource_id),
                  )
                }
              />
            ))}
          </fieldset>
          <Button
            disabled={!dataset || task.pending || saved}
            onClick={() => {
              if (!dataset || !preview || task.pending || saved) return;
              setCommand("save");
              const body = {
                run_id: runId,
                step_id: step,
                at: preview.source_at ?? at,
                case_key: key,
                expected_revision: dataset.revision,
                attachment_ids: attachments,
                knowledge_ids: knowledge,
              };
              void runTask(
                (o) =>
                  evaluationApi.fromRun(
                    dataset.id,
                    { ...body, request_id: task.requestId("capture", body) },
                    o,
                  ),
                (v) => {
                  setDataset(v);
                  setSaved(true);
                },
              );
            }}
          >
            {t("saveCase")}
          </Button>
        </section>
      )}
      {saved && dataset && (
        <Link href={`/evaluations/datasets/${dataset.id}`} className="text-sm underline">
          {t("editAndConfirm")}
        </Link>
      )}
    </div>
  );
}
