"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { evaluationApi } from "@/lib/api/evaluations";
import { inferenceApi, type InferenceModel } from "@/lib/api/inference";
import { skillsApi } from "@/lib/api/skills";
import type { Skill } from "@/lib/api/types";
import type { ConfigSelection } from "@/lib/api/types/evaluations";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { EnvironmentPicker, RecordingPicker } from "./execution-bindings";
import { Check, Choice, Field } from "./form-fields";
import { ResourcePicker } from "./resource-picker";
export const emptyConfig = (): ConfigSelection => ({
  model_id: "",
  purpose: "evaluation_subject",
  mode: "agent",
  prompt: "",
  tool_names: [],
  resources: [],
  knowledge_policy: "fixed_only",
});
export function ConfigFields({
  value,
  onChange,
  access,
}: {
  value: ConfigSelection;
  onChange: (value: ConfigSelection) => void;
  access: EvaluationAccess;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [models, setModels] = useState<InferenceModel[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  useEffect(() => {
    void runTask(
      async (o) => ({
        models: await inferenceApi.listModels(o),
        skills: await skillsApi.list(true, o),
      }),
      (v) => {
        setModels(v.models.items ?? []);
        setSkills(v.skills.skills);
      },
    );
  }, [runTask]);
  const judge = value.purpose === "evaluation_judge";
  const toolTask = useEvaluationTask(access);
  const { run: loadTools } = toolTask;
  const toolKey = JSON.stringify([value.mode ?? "agent", value.skill_id ?? ""]);
  const [toolState, setToolState] = useState<{ key: string; names: string[] }>({
    key: "",
    names: [],
  });
  const mode = value.mode ?? "agent";
  const skillId = value.skill_id ?? undefined;
  useEffect(() => {
    void loadTools(
      (o) => evaluationApi.builtinTools(mode, skillId, o),
      (items) => setToolState({ key: toolKey, names: items.map((item) => item.name) }),
      true,
    );
  }, [loadTools, mode, skillId, toolKey]);
  const tools = toolState.key === toolKey ? toolState.names : [];
  return (
    <div className="space-y-3">
      <EvaluationError error={task.error} />
      <Choice
        label={t("purpose")}
        value={value.purpose ?? "evaluation_subject"}
        onChange={(purpose) =>
          onChange(
            purpose === "evaluation_judge"
              ? {
                  ...value,
                  purpose,
                  mode: "ask",
                  skill_id: null,
                  tool_names: [],
                  resources: [],
                  external_contract_ref: null,
                }
              : { ...value, purpose: "evaluation_subject" },
          )
        }
      >
        <option value="evaluation_subject">{t("subject")}</option>
        <option value="evaluation_judge">{t("judge")}</option>
      </Choice>
      <Choice
        label={t("model")}
        value={value.model_id}
        onChange={(model_id) => onChange({ ...value, model_id })}
        required
      >
        <option value="">{t("select")}</option>
        {models
          .filter((m) => m.kind === "chat")
          .map((m) => (
            <option key={m.id} value={m.id}>
              {m.display_name || m.model_name}
            </option>
          ))}
      </Choice>
      {models.length === 0 && (
        <Link href="/admin/inference" className="text-sm underline">
          {t("missingModel")}
        </Link>
      )}
      <p className="text-muted-foreground text-sm">{t("pinExplanation")}</p>
      <Choice
        label={t("mode")}
        value={value.mode ?? "agent"}
        onChange={(mode) => onChange({ ...value, mode: mode as "agent" | "ask", tool_names: [] })}
        disabled={judge}
      >
        <option value="agent">{t("agent")}</option>
        <option value="ask">{t("ask")}</option>
      </Choice>
      <Field
        label={t("prompt")}
        value={value.prompt ?? ""}
        multiline
        onChange={(prompt) => onChange({ ...value, prompt })}
      />
      <div className="grid gap-2 sm:grid-cols-3">
        <Field
          label={t("temperature")}
          type="number"
          min={0}
          max={2}
          value={value.temperature ?? ""}
          onChange={(v) => onChange({ ...value, temperature: v === "" ? null : Number(v) })}
        />
        <Field
          label={t("maxOutputTokens")}
          type="number"
          min={1}
          value={value.max_output_tokens ?? ""}
          onChange={(v) => onChange({ ...value, max_output_tokens: v === "" ? null : Number(v) })}
        />
        <Field
          label={t("seed")}
          type="number"
          value={value.seed ?? ""}
          onChange={(v) => onChange({ ...value, seed: v === "" ? null : Number(v) })}
        />
      </div>
      {!judge && (
        <>
          <Choice
            label={t("skill")}
            value={value.skill_id ?? ""}
            onChange={(skill_id) =>
              onChange({ ...value, skill_id: skill_id || null, tool_names: [] })
            }
          >
            <option value="">{t("none")}</option>
            {skills.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Choice>
          <EvaluationError error={toolTask.error} />
          <fieldset disabled={toolTask.pending}>
            <legend>{t("tools")}</legend>
            {tools.map((tool) => (
              <Check
                key={tool}
                label={tool}
                checked={value.tool_names?.includes(tool) ?? false}
                onChange={(checked) =>
                  onChange({
                    ...value,
                    tool_names: checked
                      ? [...(value.tool_names ?? []), tool]
                      : (value.tool_names ?? []).filter((x) => x !== tool),
                  })
                }
              />
            ))}
            {tools.length === 0 && (
              <p className="text-muted-foreground text-sm">{t("toolsGuidance")}</p>
            )}
          </fieldset>
          <Choice
            label={t("externalBinding")}
            value={value.external_contract_ref?.kind ?? ""}
            onChange={(kind) =>
              onChange({
                ...value,
                external_contract_ref: kind
                  ? { kind: kind as "environment" | "recording", version_id: "" }
                  : null,
                tool_names: [],
              })
            }
          >
            <option value="">{t("none")}</option>
            <option value="recording">{t("recorded")}</option>
            <option value="environment">{t("isolated")}</option>
          </Choice>
          {value.external_contract_ref?.kind === "recording" && (
            <RecordingPicker
              multiple={false}
              access={access}
              values={
                value.external_contract_ref.version_id
                  ? [value.external_contract_ref.version_id]
                  : []
              }
              onChange={(ids, tool_names) =>
                onChange({
                  ...value,
                  tool_names: tool_names ?? [],
                  external_contract_ref: ids.length
                    ? { kind: "recording", version_id: ids[0] }
                    : null,
                })
              }
            />
          )}{" "}
          {value.external_contract_ref?.kind === "environment" && (
            <EnvironmentPicker
              access={access}
              value={value.external_contract_ref.version_id}
              onChange={(version_id, tool_names) =>
                onChange({
                  ...value,
                  tool_names: tool_names ?? [],
                  external_contract_ref: { kind: "environment", version_id },
                })
              }
            />
          )}
          <ResourcePicker
            hideAttachments
            access={access}
            attachments={[]}
            bindings={(value.resources ?? [])
              .filter((r) => r.resource_kind === "knowledge_base")
              .map((r) => ({ resource_id: r.resource_id, version_id: r.resource_version }))}
            onChange={(_, bindings) =>
              onChange({
                ...value,
                resources: bindings.map((b) => ({
                  resource_kind: "knowledge_base",
                  resource_id: b.resource_id,
                  resource_version: b.version_id,
                })),
              })
            }
          />
          <p className="text-sm">{t("fixedKnowledge")}</p>
        </>
      )}
    </div>
  );
}
