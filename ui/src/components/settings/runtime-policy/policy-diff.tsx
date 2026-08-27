"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";

export type PolicyChange = { path: string; before: unknown; after: unknown };

function flattenPolicy(value: unknown, prefix = ""): Array<[string, unknown]> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return [[prefix, value]];
  }
  return Object.keys(value as Record<string, unknown>)
    .sort()
    .flatMap((key) =>
      flattenPolicy((value as Record<string, unknown>)[key], prefix ? `${prefix}.${key}` : key),
    );
}

function deepEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function diffPolicy(before: unknown, after: unknown): PolicyChange[] {
  const beforeValues = new Map(flattenPolicy(before));
  const afterValues = new Map(flattenPolicy(after));
  return Array.from(new Set([...beforeValues.keys(), ...afterValues.keys()]))
    .sort()
    .flatMap((path) => {
      const left = beforeValues.get(path);
      const right = afterValues.get(path);
      return deepEqual(left, right) ? [] : [{ path, before: left, after: right }];
    });
}

function renderValue(value: unknown): string {
  if (value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function PolicyDiff({ before, after }: { before: unknown; after: unknown }) {
  const t = useTranslations("runtimePolicy");
  const changes = diffPolicy(before, after);
  if (changes.length === 0) {
    return <p className="text-muted-foreground text-sm">{t("diff.noChanges")}</p>;
  }
  return (
    <div className="space-y-2" aria-label={t("diff.title")}>
      {changes.map((change) => (
        <div key={change.path} className="grid gap-2 rounded-md border p-3 text-xs md:grid-cols-3">
          <Badge variant="outline" className="w-fit font-mono">
            {change.path}
          </Badge>
          <span className="text-muted-foreground break-all">{renderValue(change.before)}</span>
          <span className="text-foreground break-all">{renderValue(change.after)}</span>
        </div>
      ))}
    </div>
  );
}
