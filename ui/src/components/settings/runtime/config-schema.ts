export type ConfigFieldType = "boolean" | "number" | "string" | "enum" | "string[]";

export type ConfigFieldSchema = {
  type: ConfigFieldType;
  min?: number;
  max?: number;
  step?: number;
  options?: readonly string[];
  nullable?: boolean;
};

export type ConfigGroupSchema = {
  fields: Record<string, ConfigFieldSchema>;
};

export function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function listToLines(items?: string[] | null): string {
  return (items ?? []).join("\n");
}

export function emptyToNull(value: string): string | null {
  return value.trim() === "" ? null : value.trim();
}
