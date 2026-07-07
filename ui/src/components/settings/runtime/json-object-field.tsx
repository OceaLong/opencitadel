"use client";

import { useEffect, useState } from "react";

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";

type JsonObjectFieldProps = {
  label: string;
  description?: string;
  value: unknown;
  readOnly?: boolean;
  invalidJsonMessage: string;
  onChange: (value: unknown) => void;
  onValidityChange?: (valid: boolean) => void;
};

export function JsonObjectField({
  label,
  description,
  value,
  readOnly = false,
  invalidJsonMessage,
  onChange,
  onValidityChange,
}: JsonObjectFieldProps) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);

  useEffect(() => {
    setText(JSON.stringify(value, null, 2));
    setParseError(null);
    onValidityChange?.(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only reset when serialized value changes
  }, [value]);

  return (
    <Field>
      <FieldLabel>{label}</FieldLabel>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      <Textarea
        rows={6}
        className="font-mono text-xs"
        value={text}
        readOnly={readOnly}
        disabled={readOnly}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          try {
            onChange(JSON.parse(next));
            setParseError(null);
            onValidityChange?.(true);
          } catch {
            setParseError(invalidJsonMessage);
            onValidityChange?.(false);
          }
        }}
      />
      {parseError ? <FieldDescription className="text-destructive">{parseError}</FieldDescription> : null}
    </Field>
  );
}
