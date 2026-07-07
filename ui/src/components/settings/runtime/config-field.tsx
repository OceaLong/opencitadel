"use client";

import { useEffect, useState } from "react";

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import {
  emptyToNull,
  linesToList,
  listToLines,
  type ConfigFieldSchema,
} from "./config-schema";

type ConfigFieldProps = {
  label: string;
  description?: string;
  schema: ConfigFieldSchema;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
};

export function ConfigField({
  label,
  description,
  schema,
  value,
  readOnly = false,
  onChange,
}: ConfigFieldProps) {
  if (schema.type === "boolean") {
    return (
      <Field orientation="horizontal">
        <div className="space-y-1">
          <FieldLabel>{label}</FieldLabel>
          {description ? <FieldDescription>{description}</FieldDescription> : null}
        </div>
        <Switch
          checked={Boolean(value)}
          disabled={readOnly}
          onCheckedChange={(checked) => onChange(checked)}
        />
      </Field>
    );
  }

  if (schema.type === "enum" && schema.options) {
    return (
      <Field>
        <FieldLabel>{label}</FieldLabel>
        {description ? <FieldDescription>{description}</FieldDescription> : null}
        <Select
          value={typeof value === "string" ? value : schema.options[0]}
          disabled={readOnly}
          onValueChange={(next) => onChange(next)}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {schema.options.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
    );
  }

  if (schema.type === "string[]") {
    return (
      <StringListField
        label={label}
        description={description}
        value={Array.isArray(value) ? (value as string[]) : []}
        readOnly={readOnly}
        onChange={onChange}
      />
    );
  }

  if (schema.type === "number") {
    return (
      <Field>
        <FieldLabel>{label}</FieldLabel>
        {description ? <FieldDescription>{description}</FieldDescription> : null}
        <Input
          type="number"
          min={schema.min}
          max={schema.max}
          step={schema.step ?? 1}
          value={value === null || value === undefined ? "" : String(value)}
          readOnly={readOnly}
          disabled={readOnly}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? undefined : Number(raw));
          }}
        />
      </Field>
    );
  }

  const stringValue = value === null || value === undefined ? "" : String(value);

  return (
    <Field>
      <FieldLabel>{label}</FieldLabel>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      <Input
        type="text"
        value={stringValue}
        readOnly={readOnly}
        disabled={readOnly}
        onChange={(e) => {
          const raw = e.target.value;
          onChange(schema.nullable ? emptyToNull(raw) : raw);
        }}
      />
    </Field>
  );
}

type StringListFieldProps = {
  label: string;
  description?: string;
  value: string[];
  readOnly?: boolean;
  onChange: (value: unknown) => void;
};

function StringListField({
  label,
  description,
  value,
  readOnly = false,
  onChange,
}: StringListFieldProps) {
  const [text, setText] = useState(() => listToLines(value));

  useEffect(() => {
    setText(listToLines(value));
  }, [value]);

  return (
    <Field>
      <FieldLabel>{label}</FieldLabel>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      <Textarea
        rows={4}
        value={text}
        readOnly={readOnly}
        disabled={readOnly}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          onChange(linesToList(next));
        }}
      />
    </Field>
  );
}
