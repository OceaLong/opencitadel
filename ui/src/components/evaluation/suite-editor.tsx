"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";
import type { DatasetSummary } from "@/lib/api/types/evaluations";
import { matrixQuantity } from "@/lib/evaluation-view/validation";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { EnvironmentPicker, RecordingPicker } from "./execution-bindings";
import { Choice, Field } from "./form-fields";
import { VersionPicker } from "./version-picker";
export type SuiteDefinition = components["schemas"]["SuiteDefinition"];
export const emptySuite = (): SuiteDefinition => ({
  dataset_version: "",
  config_versions: [""],
  rubric_version: "",
  mode: "recorded",
  recording_versions: [],
  environment_version: null,
  settings: {
    repeat: 1,
    seed: 0,
    token_budget: 100000,
    case_timeout_seconds: 1800,
    batch_timeout_seconds: 86400,
    subject_concurrency: 5,
    judge_concurrency: 2,
    environment_concurrency: 2,
    max_results: 5000,
    money_budget: null,
  },
});
export function SuiteEditor({
  value,
  onChange,
  access,
}: {
  value: SuiteDefinition;
  onChange: (value: SuiteDefinition) => void;
  access: EvaluationAccess;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [dataset, setDataset] = useState("");
  const [versions, setVersions] = useState<components["schemas"]["DatasetVersionSummary"][]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  useEffect(() => {
    void runTask((o) => evaluationApi.datasets(o), setDatasets);
  }, [runTask]);
  const selected = versions.find((v) => v.id === value.dataset_version);
  const quantity = selected
    ? selected.case_count * value.config_versions.length * (value.settings.repeat ?? 1)
    : null;
  return (
    <div className="space-y-4">
      <EvaluationError error={task.error} />
      <Choice
        label={t("dataset")}
        value={dataset}
        onChange={(id) => {
          task.cancel();
          setDataset(id);
          setVersions([]);
          setCursor(null);
          onChange({ ...value, dataset_version: "" });
          if (id)
            void runTask(
              (o) => evaluationApi.datasetVersions(id, o),
              (v) => {
                setVersions(v.items);
                setCursor(v.next_cursor ?? null);
              },
              true,
            );
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
        label={t("datasetVersion")}
        value={value.dataset_version}
        onChange={(dataset_version) => onChange({ ...value, dataset_version })}
        required
      >
        <option value="">{t("select")}</option>
        {value.dataset_version && !selected && (
          <option value={value.dataset_version}>
            {t("boundVersion")} {value.dataset_version}
          </option>
        )}
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            {t("revision")} {v.revision} · {v.case_count} {t("cases")}
          </option>
        ))}
      </Choice>
      {cursor && (
        <Button
          type="button"
          variant="ghost"
          disabled={task.pending}
          onClick={() =>
            void runTask(
              (o) => evaluationApi.datasetVersions(dataset, o, cursor),
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
      <Link href="/evaluations/datasets" className="text-sm underline">
        {t("manageDatasets")}
      </Link>
      <fieldset className="space-y-2">
        <legend>{t("configurations")}</legend>
        {value.config_versions.map((id, index) => (
          <div key={index} className="space-y-1">
            <VersionPicker
              kind="configs"
              label={`${t("configuration")} ${index + 1}`}
              value={id}
              onChange={(version) =>
                onChange({
                  ...value,
                  config_versions: value.config_versions.map((v, i) => (i === index ? version : v)),
                })
              }
              access={access}
            />
            <Button
              type="button"
              variant="ghost"
              disabled={value.config_versions.length === 1}
              onClick={() =>
                onChange({
                  ...value,
                  config_versions: value.config_versions.filter((_, i) => i !== index),
                })
              }
            >
              {t("remove")}
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          disabled={value.config_versions.length >= 5}
          onClick={() => onChange({ ...value, config_versions: [...value.config_versions, ""] })}
        >
          {t("addConfiguration")}
        </Button>
      </fieldset>
      <VersionPicker
        kind="rubrics"
        label={t("rubricVersion")}
        value={value.rubric_version}
        onChange={(rubric_version) => onChange({ ...value, rubric_version })}
        access={access}
      />
      <Choice
        label={t("evaluationMode")}
        value={value.mode}
        onChange={(mode) =>
          onChange({
            ...value,
            mode: mode as "recorded" | "isolated",
            environment_version: null,
            recording_versions: [],
          })
        }
      >
        <option value="recorded">{t("recorded")}</option>
        <option value="isolated">{t("isolated")}</option>
      </Choice>
      {value.mode === "recorded" ? (
        <>
          <p className="text-sm">{t("recordingSafety")}</p>
          <RecordingPicker
            access={access}
            values={value.recording_versions ?? []}
            onChange={(recording_versions) => onChange({ ...value, recording_versions })}
          />
        </>
      ) : (
        <EnvironmentPicker
          access={access}
          value={value.environment_version ?? ""}
          onChange={(environment_version) => onChange({ ...value, environment_version })}
        />
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        {(
          [
            "repeat",
            "seed",
            "token_budget",
            "money_budget",
            "case_timeout_seconds",
            "batch_timeout_seconds",
            "subject_concurrency",
            "judge_concurrency",
            "environment_concurrency",
            "max_results",
          ] as const
        ).map((key) => (
          <Field
            key={key}
            label={t(key)}
            type="number"
            min={key === "seed" ? undefined : key === "money_budget" ? 0.01 : 1}
            step={key === "money_budget" ? "any" : 1}
            max={
              key === "repeat"
                ? Math.min(
                    5,
                    selected
                      ? Math.floor(5000 / (selected.case_count * value.config_versions.length))
                      : 5,
                  )
                : key === "max_results"
                  ? 5000
                  : undefined
            }
            value={value.settings[key] ?? ""}
            onChange={(raw) =>
              onChange({
                ...value,
                settings: {
                  ...value.settings,
                  [key]: raw === "" && key === "money_budget" ? null : Number(raw),
                },
              })
            }
            required={key !== "money_budget"}
          />
        ))}
      </div>
      <p role="status" className="text-sm">
        {t("matrixLimit")}{" "}
        {quantity === null
          ? t("unknown")
          : `${selected?.case_count} × ${value.config_versions.length} × ${value.settings.repeat ?? 1} = ${quantity}`}
      </p>
      {selected &&
        matrixQuantity(
          selected.case_count,
          value.config_versions.length,
          value.settings.repeat ?? 1,
        ) === null && <p role="alert">{t("matrixExceeded")}</p>}
    </div>
  );
}
