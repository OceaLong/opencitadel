"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";
import type {
  CaseRevision,
  DatasetDraft,
  DatasetVersion,
  ImportPreview as Preview,
} from "@/lib/api/types/evaluations";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Check, Choice, Field, tokens } from "./form-fields";
import { ImportPreview } from "./import-preview";
import { ResourcePicker } from "./resource-picker";
import { RuleEditor } from "./rule-editor";

type CaseInput = components["schemas"]["CaseInput"];
const emptyCase = (): CaseInput => ({
  input: "",
  history: [],
  attachments: [],
  knowledge_bindings: [],
  reference_answer: null,
  reference_confirmed: false,
  input_confirmed: false,
  rules: [],
  tags: [],
  applicable_dimensions: [],
});
function caseInput(value: CaseRevision): CaseInput {
  const {
    input,
    history,
    attachments,
    knowledge_bindings,
    reference_answer,
    reference_confirmed,
    input_confirmed,
    rules,
    tags,
    applicable_dimensions,
  } = value;
  return {
    input,
    history,
    attachments,
    knowledge_bindings,
    reference_answer,
    reference_confirmed,
    input_confirmed,
    rules,
    tags,
    applicable_dimensions,
  };
}

export function DatasetEditor({ id, access }: { id: string; access: EvaluationAccess }) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [draft, setDraft] = useState<DatasetDraft | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [versions, setVersions] = useState<components["schemas"]["DatasetVersionSummary"][]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [fixed, setFixed] = useState<DatasetVersion | null>(null);
  const [caseKey, setCaseKey] = useState("");
  const [value, setValue] = useState<CaseInput>(emptyCase);
  const [selected, setSelected] = useState<CaseRevision | null>(null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const [dirty, setDirty] = useState(false);
  const adopt = (next: DatasetDraft) => {
    setDraft(next);
    setPreview(null);
    setFixed(null);
    setDirty(false);
  };
  const refresh = useCallback(() => {
    void runTask(
      async (o) => ({
        draft: await evaluationApi.dataset(id, o),
        history: await evaluationApi.datasetVersions(id, o),
      }),
      (result) => {
        setDraft(result.draft);
        setVersions(result.history.items);
        setCursor(result.history.next_cursor ?? null);
        setPreview(null);
        setFixed(null);
        setSelected(null);
        setValue(emptyCase());
        setDirty(false);
      },
      true,
    );
  }, [id, runTask]);
  useEffect(refresh, [refresh]);
  if (task.error === "denied") return <EvaluationError error="denied" />;
  const cases = (fixed?.cases ?? draft?.cases ?? []).filter((c) =>
    c.case_key.toLowerCase().includes(filter.toLowerCase()),
  );
  const edit = (next: CaseInput) => {
    setValue(next);
    setDirty(true);
    setPreview(null);
  };
  const disabled = task.pending || !access.canManage || !!fixed;
  return (
    <>
      <Link href="/evaluations" className="text-sm underline">
        {t("back")}
      </Link>
      <h1 className="text-xl font-semibold">{draft?.name ?? t("dataset")}</h1>
      <EvaluationError error={task.error} refresh={refresh} />
      {!draft ? (
        <p role="status">{t("loading")}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm">
              {t("revision")} {draft.revision} · {t("cases")} {draft.cases.length}
            </span>
            <Button
              variant="outline"
              disabled={disabled || dirty || draft.cases.length === 0}
              onClick={() => {
                const body = { expected_revision: draft.revision };
                void runTask(
                  async (o) => {
                    await evaluationApi.publishDataset(
                      id,
                      { ...body, request_id: task.requestId("publish", body) },
                      o,
                    );
                    return {
                      draft: await evaluationApi.dataset(id, o),
                      history: await evaluationApi.datasetVersions(id, o),
                    };
                  },
                  (v) => {
                    adopt(v.draft);
                    setVersions(v.history.items);
                    setCursor(v.history.next_cursor ?? null);
                  },
                );
              }}
            >
              {t("publish")}
            </Button>
            <Button variant="ghost" onClick={refresh} disabled={task.pending}>
              {t("refresh")}
            </Button>
          </div>
          <section className="space-y-2">
            <h2 className="font-semibold">{t("history")}</h2>
            <Choice
              label={t("fixedVersion")}
              disabled={task.pending}
              value={fixed?.id ?? ""}
              onChange={(version) => {
                if (task.pending) return;
                task.cancel();
                setSelected(null);
                setCaseKey("");
                setValue(emptyCase());
                setDirty(false);
                setPreview(null);
                if (!version) {
                  setFixed(null);
                  return;
                }
                void runTask((o) => evaluationApi.datasetVersion(version, o), setFixed, true);
              }}
            >
              <option value="">{t("draft")}</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {t("revision")} {v.revision} · {v.case_count} {t("cases")}
                </option>
              ))}
            </Choice>
            {cursor && (
              <Button
                variant="outline"
                disabled={task.pending}
                onClick={() =>
                  void runTask(
                    (o) => evaluationApi.datasetVersions(id, o, cursor),
                    (v) => {
                      setVersions((old) => [...old, ...v.items]);
                      setCursor(v.next_cursor ?? null);
                    },
                  )
                }
              >
                {t("more")}
              </Button>
            )}
            {fixed && <p role="status">{t("immutable")}</p>}
          </section>
          {!fixed && (
            <section className="space-y-2 rounded-md border p-3">
              <label className="block text-sm font-medium">
                {t("uploadImport")}
                <input
                  type="file"
                  accept=".json,.csv,application/json,text/csv"
                  disabled={disabled}
                  className="block min-h-9 w-full text-sm"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    setPreview(null);
                    if (file)
                      void runTask(
                        (o) =>
                          evaluationApi.validateImport(
                            id,
                            file,
                            crypto.randomUUID(),
                            draft.revision,
                            o,
                          ),
                        setPreview,
                        true,
                      );
                    e.target.value = "";
                  }}
                />
              </label>
              <p className="text-muted-foreground text-sm">{t("importLimits")}</p>
            </section>
          )}
          {preview && (
            <ImportPreview
              preview={preview}
              revision={draft.revision}
              pending={task.pending}
              canManage={access.canManage}
              onApply={() => {
                const body = {
                  expected_revision: draft.revision,
                  input_digest: preview.input_digest,
                };
                void runTask(
                  (o) =>
                    evaluationApi.applyImport(
                      id,
                      preview.import_id,
                      { ...body, request_id: task.requestId("import:" + preview.import_id, body) },
                      o,
                    ),
                  adopt,
                );
              }}
            />
          )}
          <Field
            label={t("searchCases")}
            value={filter}
            onChange={(v) => {
              setFilter(v);
              setPage(0);
            }}
          />
          <div className="max-h-72 overflow-auto rounded-md border">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>
                  <th className="p-2">{t("caseKey")}</th>
                  <th>{t("inputStatus")}</th>
                  <th>{t("reference")}</th>
                  <th>
                    <span className="sr-only">{t("edit")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {cases.slice(page * 50, (page + 1) * 50).map((c) => (
                  <tr key={c.id} className="border-t">
                    <td className="max-w-44 p-2 break-words">{c.case_key}</td>
                    <td>{t(c.input_status ?? "provided")}</td>
                    <td>{t(c.reference_confirmed ? "confirmed" : "unconfirmed")}</td>
                    <td>
                      <Button
                        variant="ghost"
                        disabled={task.pending}
                        onClick={() => {
                          if (task.pending) return;
                          setSelected(c);
                          setCaseKey(c.case_key);
                          setValue(caseInput(c));
                          setDirty(false);
                          setPreview(null);
                        }}
                      >
                        {t(fixed ? "inspect" : "edit")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cases.length === 0 && <p>{t(filter ? "filteredEmpty" : "empty")}</p>}
          <div className="flex gap-2">
            <Button variant="outline" disabled={page === 0} onClick={() => setPage((v) => v - 1)}>
              {t("previous")}
            </Button>
            <Button
              variant="outline"
              disabled={(page + 1) * 50 >= cases.length}
              onClick={() => setPage((v) => v + 1)}
            >
              {t("next")}
            </Button>
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() => {
                setSelected(null);
                setCaseKey("");
                setValue(emptyCase());
                setDirty(false);
              }}
            >
              {t("newCase")}
            </Button>
          </div>
          <form
            className="space-y-4 rounded-md border p-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (disabled || !caseKey.trim()) return;
              const body = { expected_revision: draft.revision, case: value };
              void runTask(
                (o) =>
                  evaluationApi.updateCase(
                    id,
                    caseKey,
                    { ...body, request_id: task.requestId("case:" + caseKey, body) },
                    o,
                  ),
                (next) => {
                  adopt(next);
                  const saved = next.cases.find((c) => c.case_key === caseKey);
                  if (saved) {
                    setSelected(saved);
                    setValue(caseInput(saved));
                  }
                },
              );
            }}
          >
            <h2 className="font-semibold">{t(selected ? "editCase" : "newCase")}</h2>
            <fieldset disabled={disabled} className="space-y-3">
              <Field
                label={t("caseKey")}
                value={caseKey}
                onChange={setCaseKey}
                required
                disabled={!!selected}
              />
              <Choice
                label={t("inputFormat")}
                value={typeof value.input === "string" ? "text" : "messages"}
                onChange={(format) => edit({ ...value, input: format === "text" ? "" : [] })}
              >
                <option value="text">{t("text")}</option>
                <option value="messages">{t("messages")}</option>
              </Choice>
              {typeof value.input === "string" ? (
                <Field
                  label={t("input")}
                  value={value.input}
                  multiline
                  required
                  onChange={(input) => edit({ ...value, input, input_confirmed: false })}
                />
              ) : (
                <Messages
                  label={t("input")}
                  messages={value.input}
                  onChange={(input) => edit({ ...value, input, input_confirmed: false })}
                />
              )}
              <Messages
                label={t("conversationHistory")}
                messages={value.history ?? []}
                onChange={(history) => edit({ ...value, history, input_confirmed: false })}
              />
              {selected?.source_run_id && (
                <p className="text-sm">
                  {t("sourceRun")}: {selected.source_run_id} ·{" "}
                  {t(selected.input_status ?? "provided")}
                </p>
              )}
              <Check
                label={t("confirmInput")}
                checked={value.input_confirmed ?? false}
                onChange={(input_confirmed) => edit({ ...value, input_confirmed })}
              />
              {selected?.reference_candidate && (
                <details>
                  <summary>{t("candidate")}</summary>
                  <pre className="max-h-48 overflow-auto text-sm break-words whitespace-pre-wrap">
                    {selected.reference_candidate}
                  </pre>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      edit({
                        ...value,
                        reference_answer: selected.reference_candidate,
                        reference_confirmed: false,
                      })
                    }
                  >
                    {t("useCandidate")}
                  </Button>
                </details>
              )}
              <Field
                label={t("reference")}
                multiline
                value={value.reference_answer ?? ""}
                onChange={(reference_answer) =>
                  edit({
                    ...value,
                    reference_answer: reference_answer || null,
                    reference_confirmed: false,
                  })
                }
              />
              <Check
                label={t("confirmReference")}
                checked={value.reference_confirmed ?? false}
                onChange={(reference_confirmed) => edit({ ...value, reference_confirmed })}
              />
              <Field
                label={t("tags")}
                value={(value.tags ?? []).join(", ")}
                onChange={(tags) => edit({ ...value, tags: tokens(tags) })}
              />
              <Field
                label={t("applicableDimensions")}
                value={(value.applicable_dimensions ?? []).join(", ")}
                onChange={(v) => edit({ ...value, applicable_dimensions: tokens(v) })}
              />
              <ResourcePicker
                key={`${fixed?.id ?? "draft"}:${selected?.id ?? "new"}:${caseKey}`}
                access={access}
                attachments={value.attachments ?? []}
                bindings={value.knowledge_bindings ?? []}
                onChange={(attachments, knowledge_bindings) =>
                  edit({ ...value, attachments, knowledge_bindings })
                }
                disabled={disabled}
              />
              <RuleEditor
                key={`rules:${fixed?.id ?? "draft"}:${selected?.id ?? "new"}:${caseKey}`}
                rules={value.rules ?? []}
                onChange={(rules) => edit({ ...value, rules: rules as CaseInput["rules"] })}
              />
              <Button type="submit" disabled={disabled}>
                {t("saveCase")}
              </Button>
            </fieldset>
          </form>
        </>
      )}
    </>
  );
}
function Messages({
  label,
  messages,
  onChange,
}: {
  label: string;
  messages: components["schemas"]["ConversationMessage"][];
  onChange: (messages: components["schemas"]["ConversationMessage"][]) => void;
}) {
  const t = useTranslations("evaluations");
  return (
    <fieldset className="space-y-2">
      <legend>{label}</legend>
      {messages.map((m, i) => (
        <div key={i} className="grid gap-2 sm:grid-cols-[8rem_1fr_auto]">
          <Choice
            label={t("role")}
            value={m.role}
            onChange={(role) =>
              onChange(
                messages.map((v, j) =>
                  j === i ? { ...v, role: role as "user" | "assistant" } : v,
                ),
              )
            }
          >
            <option value="user">{t("user")}</option>
            <option value="assistant">{t("assistant")}</option>
          </Choice>
          <Field
            label={t("message")}
            value={m.content}
            onChange={(content) =>
              onChange(messages.map((v, j) => (j === i ? { ...v, content } : v)))
            }
            required
            multiline
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => onChange(messages.filter((_, j) => j !== i))}
          >
            {t("remove")}
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        onClick={() => onChange([...messages, { role: "user", content: "" }])}
      >
        {t("addMessage")}
      </Button>
    </fieldset>
  );
}
