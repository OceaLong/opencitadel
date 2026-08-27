"use client";

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

export type PolicyFieldDefinition = {
  path: string;
  type: "boolean" | "number" | "string" | "enum" | "string-list";
  min?: number;
  max?: number;
  step?: number;
  options?: readonly string[];
};

export type PolicyGroupDefinition = {
  key: string;
  fields: readonly PolicyFieldDefinition[];
};

export function readPolicyPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, segment) => {
    if (typeof current !== "object" || current === null || Array.isArray(current)) return undefined;
    return (current as Record<string, unknown>)[segment];
  }, value);
}

export function writePolicyPath<T>(value: T, path: string, next: unknown): T {
  const clone = structuredClone(value) as Record<string, unknown>;
  const segments = path.split(".");
  let target = clone;
  for (const segment of segments.slice(0, -1)) {
    const child = target[segment];
    if (typeof child !== "object" || child === null || Array.isArray(child)) {
      target[segment] = {};
    }
    target = target[segment] as Record<string, unknown>;
  }
  target[segments.at(-1)!] = next;
  return clone as T;
}

export function PolicyField({
  definition,
  label,
  description,
  value,
  disabled,
  onChange,
}: {
  definition: PolicyFieldDefinition;
  label: string;
  description?: string;
  value: unknown;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) {
  const sharedLabel = (
    <div className="space-y-1">
      <FieldLabel>{label}</FieldLabel>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
    </div>
  );

  if (definition.type === "boolean") {
    return (
      <Field orientation="horizontal">
        {sharedLabel}
        <Switch
          aria-label={label}
          checked={Boolean(value)}
          disabled={disabled}
          onCheckedChange={onChange}
        />
      </Field>
    );
  }

  if (definition.type === "enum") {
    return (
      <Field>
        {sharedLabel}
        <Select
          value={typeof value === "string" ? value : definition.options?.[0]}
          disabled={disabled}
          onValueChange={onChange}
        >
          <SelectTrigger aria-label={label}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {definition.options?.map((option) => (
              <SelectItem key={option} value={option} translate="no">
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
    );
  }

  if (definition.type === "string-list") {
    return (
      <Field>
        {sharedLabel}
        <Textarea
          aria-label={label}
          rows={4}
          value={Array.isArray(value) ? value.join("\n") : ""}
          disabled={disabled}
          onChange={(event) =>
            onChange(
              event.target.value
                .split("\n")
                .map((item) => item.trim())
                .filter(Boolean),
            )
          }
        />
      </Field>
    );
  }

  return (
    <Field>
      {sharedLabel}
      <Input
        aria-label={label}
        type={definition.type === "number" ? "number" : "text"}
        min={definition.min}
        max={definition.max}
        step={definition.step}
        value={value === undefined || value === null ? "" : String(value)}
        disabled={disabled}
        onChange={(event) =>
          onChange(
            definition.type === "number"
              ? event.target.value === ""
                ? undefined
                : Number(event.target.value)
              : event.target.value,
          )
        }
      />
    </Field>
  );
}
