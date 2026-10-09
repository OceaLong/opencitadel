"use client";
import { type ReactNode, useId } from "react";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
export function Field({
  label,
  value,
  onChange,
  multiline = false,
  type = "text",
  min,
  max,
  step,
  required = false,
  disabled = false,
  hint,
}: {
  label: string;
  value: string | number;
  onChange: (value: string) => void;
  multiline?: boolean;
  type?: string;
  min?: number;
  max?: number;
  step?: number | "any";
  required?: boolean;
  disabled?: boolean;
  hint?: string;
}) {
  const id = useId();
  const props = {
    id,
    value,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      onChange(e.target.value),
    required,
    disabled,
    "aria-describedby": hint ? `${id}-hint` : undefined,
  };
  return (
    <div className="min-w-0 space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {multiline ? (
        <Textarea {...props} rows={4} />
      ) : (
        <Input
          {...props}
          type={type}
          step={step ?? (type === "number" ? "any" : undefined)}
          min={min}
          max={max}
          className="min-h-9"
        />
      )}
      {hint && (
        <p id={`${id}-hint`} className="text-muted-foreground text-sm">
          {hint}
        </p>
      )}
    </div>
  );
}
export function Choice({
  label,
  value,
  onChange,
  children,
  disabled = false,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  disabled?: boolean;
  required?: boolean;
}) {
  const id = useId();
  return (
    <div className="min-w-0 space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        required={required}
        className="border-input bg-background min-h-9 w-full min-w-0 rounded-md border px-2 text-sm"
      >
        {children}
      </select>
    </div>
  );
}
export function Check({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex min-h-9 items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
      <span>{label}</span>
    </label>
  );
}
export function tokens(value: string) {
  return value
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}
