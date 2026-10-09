"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { Check, Choice, Field, tokens } from "./form-fields";
type Rule = { [key: string]: unknown };
function defaultRule(kind: string, id: unknown): Rule {
  const fields: Record<string, Rule> = {
    text_exact: { expected: "" },
    text_normalized: { expected: "" },
    json_schema: { schema: {} },
    jsonpath: { path: "$.value", op: "eq", expected: "" },
    required_fields: { fields: ["$.value"] },
    citations: { sources: [] },
    artifact: { artifact_kind: "doc", schema: {} },
  };
  return { kind, id, ...fields[kind] };
}
export function RuleEditor({
  rules,
  onChange,
  disabled = false,
}: {
  rules: Rule[];
  onChange: (value: Rule[]) => void;
  disabled?: boolean;
}) {
  const t = useTranslations("evaluations");
  // Editor keys are separate from editable business IDs and survive our immutable edits.
  const [identity, setIdentity] = useState(() => ({
    source: rules,
    keys: rules.map((_, index) => index),
    nextKey: rules.length,
  }));
  let current = identity;
  if (identity.source !== rules) {
    const used = new Set<number>();
    let nextKey = identity.nextKey;
    const keys = rules.map((rule) => {
      let previous = identity.source.findIndex((old, i) => !used.has(i) && old === rule);
      if (previous < 0 && rule.id != null && rules.filter((r) => r.id === rule.id).length === 1) {
        const matches = identity.source.flatMap((old, i) => (old.id === rule.id ? [i] : []));
        if (matches.length === 1 && !used.has(matches[0])) previous = matches[0];
      }
      if (previous < 0) return nextKey++;
      used.add(previous);
      return identity.keys[previous];
    });
    current = { source: rules, keys, nextKey };
    setIdentity(current);
  }
  const replace = (next: Rule[], keys: number[], nextKey = current.nextKey) => {
    setIdentity({ source: next, keys, nextKey });
    onChange(next);
  };
  const change = (index: number, rule: Rule) =>
    replace(
      rules.map((r, i) => (i === index ? rule : r)),
      current.keys,
    );
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <legend className="text-sm font-semibold">{t("rules")}</legend>
      {rules.map((rule, index) => (
        <div key={current.keys[index]} className="space-y-2 rounded-md border p-3">
          <Choice
            label={t("ruleKind")}
            value={String(rule.kind)}
            onChange={(kind) => change(index, defaultRule(kind, rule.id))}
          >
            {[
              "text_exact",
              "text_normalized",
              "json_schema",
              "jsonpath",
              "required_fields",
              "citations",
              "artifact",
            ].map((k) => (
              <option key={k} value={k}>
                {t(k as "text_exact")}
              </option>
            ))}
          </Choice>
          <Field
            label={t("ruleId")}
            value={String(rule.id ?? "")}
            onChange={(id) => change(index, { ...rule, id })}
          />
          <Check
            label={t("mandatory")}
            checked={rule.required === true}
            onChange={(required) => change(index, { ...rule, required })}
          />
          <Check
            label={t("referenceRequired")}
            checked={rule.reference_required === true}
            onChange={(reference_required) => change(index, { ...rule, reference_required })}
          />
          {["text_exact", "text_normalized"].includes(String(rule.kind)) && (
            <Field
              label={t("expected")}
              value={String(rule.expected ?? "")}
              onChange={(expected) => change(index, { ...rule, expected })}
            />
          )}
          {rule.kind === "jsonpath" && rule.op !== "exists" && (
            <JsonValueField
              key={String(rule.kind)}
              label={t("expected")}
              value={rule.expected}
              onChange={(expected) => change(index, { ...rule, expected })}
            />
          )}
          {rule.kind === "jsonpath" && (
            <>
              <Field
                label={t("jsonpath")}
                value={String(rule.path ?? "")}
                onChange={(path) => change(index, { ...rule, path })}
              />
              <Choice
                label={t("operator")}
                value={String(rule.op ?? "eq")}
                onChange={(op) => change(index, { ...rule, op })}
              >
                {["eq", "ne", "gt", "gte", "lt", "lte", "exists"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </Choice>
            </>
          )}
          {rule.kind === "required_fields" && (
            <Field
              label={t("requiredFields")}
              value={((rule.fields as string[]) ?? []).join(",")}
              onChange={(value) => change(index, { ...rule, fields: tokens(value) })}
            />
          )}
          {rule.kind === "citations" && <p className="text-sm">{t("citationMeaning")}</p>}
          {rule.kind === "artifact" && (
            <Choice
              label={t("artifactKind")}
              value={String(rule.artifact_kind ?? "doc")}
              onChange={(artifact_kind) => change(index, { ...rule, artifact_kind })}
            >
              <option value="doc">{t("document")}</option>
              <option value="web">{t("web")}</option>
            </Choice>
          )}
          {(rule.kind === "json_schema" || rule.kind === "artifact") && (
            <JsonValueField
              key={String(rule.kind)}
              label={t("schema")}
              value={rule.schema}
              onChange={(schema) => change(index, { ...rule, schema })}
            />
          )}
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              replace(
                rules.filter((_, i) => i !== index),
                current.keys.filter((_, i) => i !== index),
              )
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
          replace(
            [...rules, { kind: "text_exact", id: `rule-${rules.length + 1}`, expected: "" }],
            [...current.keys, current.nextKey],
            current.nextKey + 1,
          )
        }
      >
        {t("addRule")}
      </Button>
    </fieldset>
  );
}
function JsonValueField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const t = useTranslations("evaluations");
  const serialized = JSON.stringify(value ?? {}, null, 2);
  const [draft, setDraft] = useState({ source: serialized, text: serialized, invalid: false });
  const textarea = useRef<HTMLTextAreaElement>(null);
  // Parent echoes preserve formatting/invalid drafts; a different external value replaces them.
  if (draft.source !== serialized) {
    setDraft({ source: serialized, text: serialized, invalid: false });
  }
  useEffect(() => {
    textarea.current?.setCustomValidity(draft.invalid ? t("invalidJson") : "");
  }, [draft.invalid, t]);
  return (
    <label className="block text-sm">
      {label}
      <textarea
        className="bg-background min-h-24 w-full rounded-md border p-2"
        ref={textarea}
        value={draft.text}
        onChange={(event) => {
          const text = event.target.value;
          try {
            const parsed: unknown = JSON.parse(text);
            setDraft({ source: JSON.stringify(parsed ?? {}, null, 2), text, invalid: false });
            onChange(parsed);
          } catch {
            setDraft({ source: serialized, text, invalid: true });
          }
        }}
      />
    </label>
  );
}
