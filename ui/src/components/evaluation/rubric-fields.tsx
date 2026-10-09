"use client";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import type { components } from "@/lib/api/generated/schema";

import type { EvaluationAccess } from "./evaluation-boundary";
import { Check, Choice, Field } from "./form-fields";
import { VersionPicker } from "./version-picker";
export type RubricDefinition = components["schemas"]["RubricDefinition"];
export const emptyRubric = (
  t: ReturnType<typeof useTranslations<"evaluations">>,
): RubricDefinition => ({
  judge_config_version: "",
  reference_policy: "optional",
  reference_dimensions: [],
  required_conditions: [],
  dimensions: [
    {
      id: "correctness",
      name: t("defaultCorrectness"),
      anchors: [
        t("correctness0"),
        t("correctness1"),
        t("correctness2"),
        t("correctness3"),
        t("correctness4"),
      ],
      evidence_required: false,
    },
    {
      id: "completeness",
      name: t("defaultCompleteness"),
      anchors: [
        t("completeness0"),
        t("completeness1"),
        t("completeness2"),
        t("completeness3"),
        t("completeness4"),
      ],
      evidence_required: false,
    },
    {
      id: "source_support",
      name: t("defaultSourceSupport"),
      anchors: [
        t("sourceSupport0"),
        t("sourceSupport1"),
        t("sourceSupport2"),
        t("sourceSupport3"),
        t("sourceSupport4"),
      ],
      evidence_required: true,
    },
  ],
});
export function RubricFields({
  value,
  onChange,
  access,
}: {
  value: RubricDefinition;
  onChange: (value: RubricDefinition) => void;
  access: EvaluationAccess;
}) {
  const t = useTranslations("evaluations");
  const dimensions = value.dimensions ?? [];
  return (
    <div className="space-y-4">
      <VersionPicker
        kind="configs"
        label={t("judgeConfig")}
        value={value.judge_config_version}
        onChange={(judge_config_version) => onChange({ ...value, judge_config_version })}
        access={access}
        judgeOnly
      />
      <Choice
        label={t("referencePolicy")}
        value={value.reference_policy ?? "optional"}
        onChange={(policy) =>
          onChange({ ...value, reference_policy: policy as RubricDefinition["reference_policy"] })
        }
      >
        {["optional", "required", "required_when_applicable"].map((p) => (
          <option key={p} value={p}>
            {t(p as "optional")}
          </option>
        ))}
      </Choice>
      {dimensions.map((dimension, index) => (
        <fieldset key={index} className="space-y-2 rounded-md border p-3">
          <legend className="px-1">
            {t("dimension")} {index + 1}
          </legend>
          <Field
            label={t("dimensionId")}
            value={dimension.id}
            required
            onChange={(id) => {
              const old = dimension.id;
              onChange({
                ...value,
                dimensions: dimensions.map((v, i) => (i === index ? { ...v, id } : v)),
                reference_dimensions: value.reference_dimensions?.map((v) => (v === old ? id : v)),
                required_conditions: value.required_conditions?.map((c) =>
                  c.dimension_id === old ? { ...c, dimension_id: id } : c,
                ),
              });
            }}
          />
          <Field
            label={t("name")}
            value={dimension.name}
            required
            onChange={(name) =>
              onChange({
                ...value,
                dimensions: dimensions.map((v, i) => (i === index ? { ...v, name } : v)),
              })
            }
          />
          <Check
            label={t("evidenceRequired")}
            checked={dimension.evidence_required ?? false}
            onChange={(evidence_required) =>
              onChange({
                ...value,
                dimensions: dimensions.map((v, i) =>
                  i === index ? { ...v, evidence_required } : v,
                ),
              })
            }
          />
          <Check
            label={t("referenceDimension")}
            checked={value.reference_dimensions?.includes(dimension.id) ?? false}
            onChange={(checked) =>
              onChange({
                ...value,
                reference_dimensions: checked
                  ? [...(value.reference_dimensions ?? []), dimension.id]
                  : value.reference_dimensions?.filter((v) => v !== dimension.id),
              })
            }
          />
          {dimension.anchors.map((anchor, score) => (
            <Field
              key={score}
              label={`${t("scoreAnchor")} ${score}`}
              value={anchor}
              required
              multiline
              onChange={(text) =>
                onChange({
                  ...value,
                  dimensions: dimensions.map((v, i) =>
                    i === index
                      ? {
                          ...v,
                          anchors: v.anchors.map((a, s) =>
                            s === score ? text : a,
                          ) as typeof v.anchors,
                        }
                      : v,
                  ),
                })
              }
            />
          ))}
          <Button
            type="button"
            variant="outline"
            disabled={dimensions.length === 1}
            onClick={() =>
              onChange({
                ...value,
                dimensions: dimensions.filter((_, i) => i !== index),
                reference_dimensions: value.reference_dimensions?.filter((v) => v !== dimension.id),
                required_conditions: value.required_conditions?.filter(
                  (v) => v.dimension_id !== dimension.id,
                ),
              })
            }
          >
            {t("remove")}
          </Button>
        </fieldset>
      ))}
      <Button
        type="button"
        variant="outline"
        disabled={dimensions.length >= 20}
        onClick={() =>
          onChange({
            ...value,
            dimensions: [
              ...dimensions,
              {
                id: `dimension-${dimensions.length + 1}`,
                name: "",
                anchors: ["", "", "", "", ""],
                evidence_required: false,
              },
            ],
          })
        }
      >
        {t("addDimension")}
      </Button>
      <fieldset className="space-y-2">
        <legend>{t("requiredConditions")}</legend>
        {(value.required_conditions ?? []).map((condition, index) => (
          <div key={index} className="grid gap-2 sm:grid-cols-4">
            <Choice
              label={t("dimension")}
              value={condition.dimension_id}
              onChange={(dimension_id) =>
                onChange({
                  ...value,
                  required_conditions: value.required_conditions?.map((v, i) =>
                    i === index ? { ...v, dimension_id } : v,
                  ),
                })
              }
            >
              {dimensions.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name || d.id}
                </option>
              ))}
            </Choice>
            <Choice
              label={t("scoreSource")}
              value={condition.source}
              onChange={(source) =>
                onChange({
                  ...value,
                  required_conditions: value.required_conditions?.map((v, i) =>
                    i === index ? { ...v, source: source as "model" | "human" } : v,
                  ),
                })
              }
            >
              <option value="model">{t("model")}</option>
              <option value="human">{t("human")}</option>
            </Choice>
            <Field
              label={t("minimum")}
              type="number"
              min={0}
              max={4}
              value={condition.minimum}
              onChange={(v) =>
                onChange({
                  ...value,
                  required_conditions: value.required_conditions?.map((c, i) =>
                    i === index ? { ...c, minimum: Number(v) } : c,
                  ),
                })
              }
            />
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                onChange({
                  ...value,
                  required_conditions: value.required_conditions?.filter((_, i) => i !== index),
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
          onClick={() =>
            onChange({
              ...value,
              required_conditions: [
                ...(value.required_conditions ?? []),
                { dimension_id: dimensions[0]?.id ?? "", source: "model", minimum: 3 },
              ],
            })
          }
        >
          {t("addCondition")}
        </Button>
      </fieldset>
    </div>
  );
}
