"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { type Collection, evaluationApi } from "@/lib/api/evaluations";
import type {
  ConfigurationDraft,
  ConfigurationPage,
  ConfigVersion,
  PreflightResult,
  RubricVersion,
  SuiteVersion,
} from "@/lib/api/types/evaluations";

import { ConfigFields, emptyConfig } from "./config-fields";
import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Choice, Field } from "./form-fields";
import { PreflightPanel } from "./preflight-panel";
import { emptyRubric, type RubricDefinition, RubricFields } from "./rubric-fields";
import { emptySuite, type SuiteDefinition, SuiteEditor } from "./suite-editor";
export function VersionEditor({
  kind,
  id,
  access,
}: {
  kind: Collection;
  id: string;
  access: EvaluationAccess;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [draft, setDraft] = useState<ConfigurationDraft | null>(null);
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<ConfigurationDraft["definition"]>(() =>
    kind === "configs" ? emptyConfig() : kind === "rubrics" ? emptyRubric(t) : emptySuite(),
  );
  const [versions, setVersions] = useState<ConfigurationPage["items"]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [fixed, setFixed] = useState<ConfigVersion | RubricVersion | SuiteVersion | null>(null);
  const [dirty, setDirty] = useState(false);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [started, setStarted] = useState<string | null>(null);
  const entityId = draft?.id ?? (id === "new" ? null : id);
  const reload = useCallback(() => {
    if (!entityId) return;
    void runTask(
      async (o) => ({
        draft: await evaluationApi.draft(kind, entityId, o),
        history: await evaluationApi.list(kind, o, true, undefined, entityId),
      }),
      (v) => {
        setDraft(v.draft);
        setDefinition(v.draft.definition);
        setName(v.draft.name);
        setVersions(v.history.items);
        setCursor(v.history.next_cursor ?? null);
        setDirty(false);
        setFixed(null);
        setPreflight(null);
      },
      true,
    );
  }, [entityId, kind, runTask]);
  useEffect(reload, [reload]);
  if (task.error === "denied") return <EvaluationError error="denied" />;
  const change = (definition: ConfigurationDraft["definition"]) => {
    setDefinition(definition);
    setDirty(true);
    setPreflight(null);
    setStarted(null);
  };
  const entity = draft?.id;
  return (
    <>
      <Link href={`/evaluations/${kind}`} className="text-sm underline">
        {t("back")}
      </Link>
      <h1 className="text-xl font-semibold">
        {t(kind)} · {name || t("new")}
      </h1>
      <EvaluationError error={task.error} refresh={reload} />
      {entity && (
        <section className="space-y-2">
          <p className="text-sm">
            {t("revision")} {draft.revision}
          </p>
          <Choice
            label={t("history")}
            value={fixed?.id ?? ""}
            onChange={(version) => {
              task.cancel();
              setPreflight(null);
              setStarted(null);
              if (!version) {
                setFixed(null);
                return;
              }
              void runTask((o) => evaluationApi.version(kind, version, o), setFixed, true);
            }}
          >
            <option value="">{t("draft")}</option>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name} · {t("revision")} {v.revision}
              </option>
            ))}
          </Choice>
          {cursor && (
            <Button
              variant="outline"
              disabled={task.pending}
              onClick={() =>
                void runTask(
                  (o) => evaluationApi.list(kind, o, true, cursor, entity),
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
        </section>
      )}
      {fixed ? (
        <section className="space-y-3 rounded-md border p-3">
          <h2 className="font-semibold">
            {fixed.name} · {t("revision")} {fixed.revision}
          </h2>
          <p>{t("immutable")}</p>
          {"version_unpinned" in fixed ? (
            <>
              <p>{t(fixed.version_unpinned ? "versionUnpinned" : "versionPinned")}</p>
              <p className="text-sm">{fixed.model_id}</p>
              <ul>
                {fixed.unpinned_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </>
          ) : (
            <fieldset disabled>
              {"dataset_version" in fixed ? (
                <SuiteEditor value={fixed} onChange={() => {}} access={access} />
              ) : (
                <RubricFields value={fixed} onChange={() => {}} access={access} />
              )}
            </fieldset>
          )}
        </section>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!access.canManage || task.pending) return;
            const base = {
              name,
              definition,
              ...(draft ? { expected_revision: draft.revision } : {}),
            };
            const request_id = task.requestId("save:" + (entity ?? kind), base);
            void runTask(
              (o) =>
                draft
                  ? evaluationApi.update(
                      kind,
                      draft.id,
                      { name, definition, expected_revision: draft.revision, request_id },
                      o,
                    )
                  : evaluationApi.create(kind, { name, definition, request_id }, o),
              (saved) => {
                setDraft(saved);
                setDefinition(saved.definition);
                setName(saved.name);
                setDirty(false);
                setPreflight(null);
              },
            );
          }}
          className="space-y-4 rounded-md border p-3"
        >
          <fieldset disabled={!access.canManage || task.pending} className="space-y-4">
            <Field
              label={t("name")}
              value={name}
              required
              onChange={(v) => {
                setName(v);
                setDirty(true);
                setPreflight(null);
              }}
            />
            {kind === "configs" ? (
              <ConfigFields
                value={definition as ReturnType<typeof emptyConfig>}
                onChange={change}
                access={access}
              />
            ) : kind === "rubrics" ? (
              <RubricFields
                value={definition as RubricDefinition}
                onChange={change}
                access={access}
              />
            ) : (
              <SuiteEditor
                value={definition as SuiteDefinition}
                onChange={change}
                access={access}
              />
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="submit">{t("save")}</Button>
              <Button
                type="button"
                variant="outline"
                disabled={!entity || dirty}
                onClick={() => {
                  if (!draft) return;
                  const body = { expected_revision: draft.revision };
                  void runTask(
                    async (o) => {
                      const version = await evaluationApi.publish(
                        kind,
                        draft.id,
                        { ...body, request_id: task.requestId("publish:" + draft.id, body) },
                        o,
                      );
                      return {
                        version,
                        draft: await evaluationApi.draft(kind, draft.id, o),
                        history: await evaluationApi.list(kind, o, true, undefined, draft.id),
                      };
                    },
                    (v) => {
                      setDraft(v.draft);
                      setFixed(v.version);
                      setVersions(v.history.items);
                      setCursor(v.history.next_cursor ?? null);
                      setPreflight(null);
                    },
                  );
                }}
              >
                {t("publish")}
              </Button>
            </div>
          </fieldset>
        </form>
      )}
      {kind === "suites" && fixed && "dataset_version" in fixed && (
        <PreflightPanel
          key={fixed.id}
          result={preflight}
          pending={task.pending}
          canRun={access.canRun}
          onCheck={() => {
            setPreflight(null);
            void runTask((o) => evaluationApi.preflight(fixed.id, o), setPreflight);
          }}
          onStart={() => {
            if (!preflight?.allowed || !access.canRun) return;
            const body = { suite_version: fixed.id, preflight_revision: preflight.revision };
            void runTask(
              (o) => evaluationApi.start({ ...body, request_id: task.requestId("start", body) }, o),
              (batch) => {
                setStarted(batch.id);
                setPreflight(null);
              },
            );
          }}
        />
      )}
      {started && (
        <p role="status">
          {t("batchAccepted")} <span className="break-all">{started}</span>
        </p>
      )}
    </>
  );
}
