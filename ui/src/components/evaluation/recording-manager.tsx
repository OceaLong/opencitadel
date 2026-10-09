"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import { executionViewApi } from "@/lib/api/execution-view";
import type { components } from "@/lib/api/generated/schema";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { RecordingPicker } from "./execution-bindings";
import { Check, Choice, Field } from "./form-fields";
type S = components["schemas"];
export function RecordingManager({ access }: { access: EvaluationAccess }) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [runs, setRuns] = useState<S["RunView"][]>([]);
  const [runsCursor, setRunsCursor] = useState<string | null>(null);
  const [run, setRun] = useState("");
  const [candidates, setCandidates] = useState<S["RecordingCandidate"][]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selections, setSelections] = useState<S["RecordingSelection"][]>([]);
  const [job, setJob] = useState<S["RecordingJob"] | null>(null);
  const [values, setValues] = useState<string[]>([]);
  const loadRuns = useCallback(
    (next?: string) =>
      void runTask(
        (o) => executionViewApi.listRuns({ limit: 100, cursor: next }, o),
        (v) => {
          setRuns((old) => (next ? [...old, ...v.items] : v.items));
          setRunsCursor(v.next_cursor ?? null);
        },
      ),
    [runTask],
  );
  useEffect(() => {
    loadRuns();
  }, [loadRuns]);
  const adopt = (page: S["RecordingCandidatePage"], append = false) => {
    setCandidates((old) => (append ? [...old, ...page.items] : page.items));
    setCursor(page.next_cursor ?? null);
    setSelections((old) => {
      const existing = append ? old : [];
      const names = new Set(existing.map((s) => s.tool));
      return [
        ...existing,
        ...page.items
          .filter((c) => {
            if (names.has(c.tool)) return false;
            names.add(c.tool);
            return true;
          })
          .map((c) => ({
            tool: c.tool,
            allowed_fields: c.result_fields.map((f) => f.name),
            replacements: {},
            argument_replacements: {},
            rule: { version: 1 as const, excluded_fields: [] },
          })),
      ];
    });
  };
  if (task.error === "denied") return <EvaluationError error="denied" />;
  return (
    <>
      <Link href="/evaluations" className="text-sm underline">
        {t("back")}
      </Link>
      <h1 className="text-xl font-semibold">{t("recordings")}</h1>
      <EvaluationError error={task.error} />
      <RecordingPicker access={access} values={values} onChange={setValues} />
      <p className="text-sm">{t("recordingSafety")}</p>
      {access.canManage && (
        <form
          className="space-y-3 rounded-md border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (cursor || !run || !selections.length) return;
            const body = { run_id: run, selections };
            void runTask(
              (o) =>
                evaluationApi.createRecording(
                  { ...body, request_id: task.requestId("recording", body) },
                  o,
                ),
              setJob,
            );
          }}
        >
          <fieldset disabled={task.pending} className="space-y-3">
            <Choice
              label={t("sourceRun")}
              value={run}
              onChange={(id) => {
                setRun(id);
                setCandidates([]);
                setSelections([]);
                setJob(null);
                setCursor(null);
                if (id)
                  void runTask(
                    (o) => evaluationApi.recordingCandidates(id, o),
                    (page) => adopt(page),
                    true,
                  );
              }}
              required
            >
              <option value="">{t("select")}</option>
              {runs
                .filter((r) => ["succeeded", "failed", "cancelled", "completed"].includes(r.status))
                .map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.public_summary ?? r.run_id} · {r.status}
                  </option>
                ))}
            </Choice>
            {runsCursor && (
              <Button type="button" variant="outline" onClick={() => loadRuns(runsCursor)}>
                {t("moreRuns")}
              </Button>
            )}
            {cursor && (
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  void runTask(
                    (o) => evaluationApi.recordingCandidates(run, o, cursor),
                    (page) => adopt(page, true),
                  )
                }
              >
                {t("moreSlots")}
              </Button>
            )}
            {selections.map((selection, index) => {
              const candidate = candidates.find((c) => c.tool === selection.tool)!;
              const update = (next: S["RecordingSelection"]) =>
                setSelections((old) => old.map((s, i) => (i === index ? next : s)));
              return (
                <fieldset key={selection.tool} className="space-y-2 rounded-md border p-3">
                  <legend>
                    {selection.tool} · {candidate.effect}
                  </legend>
                  {candidate.requires_argument_replacement && <p>{t("replacementRequired")}</p>}
                  <h3 className="text-sm">{t("allowedFields")}</h3>
                  {candidate.result_fields.map((field) => (
                    <div key={field.name} className="space-y-1">
                      <Check
                        label={`${field.name} (${field.type})`}
                        checked={selection.allowed_fields.includes(field.name)}
                        onChange={(checked) =>
                          update({
                            ...selection,
                            allowed_fields: checked
                              ? [...selection.allowed_fields, field.name]
                              : selection.allowed_fields.filter((f) => f !== field.name),
                          })
                        }
                      />
                      <Replacement
                        field={field}
                        value={selection.replacements?.[field.name]}
                        onChange={(value) => {
                          const next = { ...selection.replacements };
                          if (value === undefined) delete next[field.name];
                          else next[field.name] = value;
                          update({ ...selection, replacements: next });
                        }}
                      />
                    </div>
                  ))}
                  <h3 className="text-sm">{t("argumentSubstitutions")}</h3>
                  {candidate.argument_fields.map((field) => (
                    <div key={field.name} className="space-y-1">
                      <Replacement
                        field={field}
                        value={selection.argument_replacements?.[field.name]}
                        onChange={(value) => {
                          const next = { ...selection.argument_replacements };
                          if (value === undefined) delete next[field.name];
                          else next[field.name] = value;
                          update({ ...selection, argument_replacements: next });
                        }}
                      />
                      {field.nonsemantic && (
                        <Check
                          label={`${t("ignoreNonsemantic")} ${field.name}`}
                          checked={selection.rule?.excluded_fields?.includes(field.name) ?? false}
                          onChange={(checked) =>
                            update({
                              ...selection,
                              rule: {
                                version: 1,
                                excluded_fields: checked
                                  ? [...(selection.rule?.excluded_fields ?? []), field.name]
                                  : (selection.rule?.excluded_fields ?? []).filter(
                                      (f) => f !== field.name,
                                    ),
                              },
                            })
                          }
                        />
                      )}
                    </div>
                  ))}
                </fieldset>
              );
            })}
            <Button disabled={!selections.length || !!cursor}>{t("generateRecording")}</Button>
          </fieldset>
        </form>
      )}
      {job && (
        <section className="space-y-2">
          <p role="status">
            {t("recordingJob")}: {t(job.status)} · {job.error ?? ""}
          </p>
          <Button
            variant="outline"
            disabled={task.pending}
            onClick={() => void runTask((o) => evaluationApi.recording(job.id, o), setJob)}
          >
            {t("refresh")}
          </Button>
          {job.result_version && (
            <p className="text-sm break-all">
              {t("fixedVersion")}: {job.result_version}
            </p>
          )}
        </section>
      )}
    </>
  );
}
function Replacement({
  field,
  value,
  onChange,
}: {
  field: S["RecordingField"];
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const t = useTranslations("evaluations");
  const [enabled, setEnabled] = useState(value !== undefined);
  return (
    <div>
      <Check
        label={`${t("replaceField")} ${field.name}`}
        checked={enabled}
        onChange={(checked) => {
          setEnabled(checked);
          onChange(
            checked
              ? field.type === "boolean"
                ? false
                : field.type === "object"
                  ? {}
                  : field.type === "array"
                    ? []
                    : field.type === "number" || field.type === "integer"
                      ? 0
                      : ""
              : undefined,
          );
        }}
      />
      {enabled &&
        (field.type === "boolean" ? (
          <Check label={field.name} checked={value === true} onChange={onChange} />
        ) : field.type === "object" || field.type === "array" || field.type === "unknown" ? (
          <JsonReplacement label={`${field.name} (JSON)`} value={value} onChange={onChange} />
        ) : (
          <Field
            label={field.name}
            value={String(value ?? "")}
            type={field.type === "number" || field.type === "integer" ? "number" : "text"}
            onChange={(text) =>
              onChange(field.type === "number" || field.type === "integer" ? Number(text) : text)
            }
          />
        ))}
    </div>
  );
}
function JsonReplacement({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const t = useTranslations("evaluations");
  const [text, setText] = useState(JSON.stringify(value ?? {}));
  return (
    <label className="block text-sm">
      {label}
      <textarea
        className="bg-background min-h-24 w-full rounded-md border p-2"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            e.target.setCustomValidity("");
          } catch {
            e.target.setCustomValidity(t("invalidJson"));
          }
        }}
      />
    </label>
  );
}
