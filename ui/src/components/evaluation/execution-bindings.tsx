"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Check, Choice } from "./form-fields";
type S = components["schemas"];
export function RecordingPicker({
  access,
  values,
  onChange,
  multiple = true,
}: {
  access: EvaluationAccess;
  values: string[];
  multiple?: boolean;
  onChange: (ids: string[], tools?: string[]) => void;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [items, setItems] = useState<{ job: S["RecordingJob"]; result: S["RecordingResult"] }[]>(
    [],
  );
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(
    (next?: string) =>
      void runTask(
        async (o) => {
          const page = await evaluationApi.recordings(o, next);
          const ready = page.items.filter((j) => j.status === "ready");
          return {
            rows: await Promise.all(
              ready.map(async (job) => ({
                job,
                result: await evaluationApi.recordingResult(job.id, o),
              })),
            ),
            cursor: page.next_cursor,
          };
        },
        (v) => {
          setItems((old) => (next ? [...old, ...v.rows] : v.rows));
          setCursor(v.cursor ?? null);
        },
      ),
    [runTask],
  );
  useEffect(() => {
    load();
  }, [load]);
  return (
    <div className="space-y-2">
      <EvaluationError error={task.error} />
      <fieldset>
        <legend>{t("recordings")}</legend>
        {!multiple && (
          <Choice
            label={t("recordings")}
            value={values[0] ?? ""}
            onChange={(id) =>
              onChange(
                id ? [id] : [],
                items.find((i) => i.result.version_id === id)?.result.tool_names ?? [],
              )
            }
          >
            <option value="">{t("select")}</option>
            {values[0] && !items.some((i) => i.result.version_id === values[0]) && (
              <option value={values[0]}>
                {t("boundVersion")} {values[0]}
              </option>
            )}
            {items.map(({ job, result }) => (
              <option key={job.id} value={result.version_id}>
                {t("revision")} {result.revision} · {result.slot_count} {t("slots")} ·{" "}
                {result.tool_names.join(", ")}
              </option>
            ))}
          </Choice>
        )}
        {multiple &&
          items.map(({ job, result }) => (
            <Check
              key={job.id}
              label={`${t("revision")} ${result.revision} · ${result.slot_count} ${t("slots")} · ${result.tool_names.join(", ")}`}
              checked={values.includes(result.version_id)}
              onChange={(checked) => {
                const selected = checked
                  ? [...values, result.version_id]
                  : values.filter((v) => v !== result.version_id);
                onChange(selected, [
                  ...new Set(
                    items
                      .filter((item) => selected.includes(item.result.version_id))
                      .flatMap((item) => item.result.tool_names),
                  ),
                ]);
              }}
            />
          ))}
        {values
          .filter((id) => !items.some((i) => i.result.version_id === id))
          .map((id) => (
            <p key={id} className="text-sm break-all">
              {t("boundVersion")}: {id}
            </p>
          ))}
        {!items.length && !task.pending && <p>{t("empty")}</p>}
      </fieldset>
      {cursor && (
        <Button
          type="button"
          variant="outline"
          disabled={task.pending}
          onClick={() => load(cursor)}
        >
          {t("more")}
        </Button>
      )}
      <Link className="text-sm underline" href="/evaluations/recordings">
        {t("manageRecordings")}
      </Link>
    </div>
  );
}
export function EnvironmentPicker({
  access,
  value,
  onChange,
}: {
  access: EvaluationAccess;
  value: string;
  onChange: (id: string, tools?: string[]) => void;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [items, setItems] = useState<S["EnvironmentChoice"][]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(
    (next?: string) =>
      void runTask(
        (o) => evaluationApi.environments(o, next),
        (page) => {
          setItems((old) => (next ? [...old, ...page.items] : page.items));
          setCursor(page.next_cursor ?? null);
        },
      ),
    [runTask],
  );
  useEffect(() => {
    load();
  }, [load]);
  return (
    <div className="space-y-2">
      <EvaluationError error={task.error} />
      <Choice
        label={t("environment")}
        value={value}
        onChange={(id) => {
          onChange(id, items.find((i) => i.version.id === id)?.tool_names ?? []);
        }}
        required
      >
        <option value="">{t("select")}</option>
        {value && !items.some((v) => v.version.id === value) && (
          <option value={value}>
            {t("boundVersion")} {value}
          </option>
        )}
        {items.map((item) => (
          <option key={item.version.id} value={item.version.id} disabled={!item.qualified}>
            {item.version.fixture_revision} · {t("revision")} {item.version.revision} ·{" "}
            {t(item.qualified ? "qualified" : "unavailable")} · {item.version.id}
          </option>
        ))}
      </Choice>
      {cursor && (
        <Button
          type="button"
          variant="outline"
          disabled={task.pending}
          onClick={() => load(cursor)}
        >
          {t("more")}
        </Button>
      )}
      <Link href="/evaluations/environments" className="text-sm underline">
        {t("manageEnvironments")}
      </Link>
    </div>
  );
}
