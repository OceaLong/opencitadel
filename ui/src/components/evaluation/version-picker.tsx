"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { type Collection, evaluationApi } from "@/lib/api/evaluations";
import type { ConfigurationPage } from "@/lib/api/types/evaluations";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Choice } from "./form-fields";
export function VersionPicker({
  kind,
  label,
  value,
  onChange,
  access,
  disabled = false,
  judgeOnly = false,
}: {
  kind: Collection;
  label: string;
  value: string;
  onChange: (id: string) => void;
  access: EvaluationAccess;
  disabled?: boolean;
  judgeOnly?: boolean;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [rows, setRows] = useState<ConfigurationPage["items"]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const load = useCallback(
    (next?: string) =>
      void runTask(
        async (o) => {
          const page = await evaluationApi.list(kind, o, true, next);
          if (kind !== "configs") return page;
          const versions = await Promise.all(
            page.items.map((v) => evaluationApi.version(kind, v.id, o)),
          );
          return {
            ...page,
            items: page.items.filter(
              (_, i) =>
                "purpose" in versions[i] &&
                versions[i].purpose === (judgeOnly ? "evaluation_judge" : "evaluation_subject"),
            ),
          };
        },
        (page) => {
          setRows((old) => (next ? [...old, ...page.items] : page.items));
          setCursor(page.next_cursor ?? null);
        },
      ),
    [kind, judgeOnly, runTask],
  );
  useEffect(() => {
    load();
  }, [load]);
  return (
    <div className="space-y-1">
      <EvaluationError error={task.error} />
      <Choice
        label={label}
        value={value}
        onChange={onChange}
        disabled={disabled || task.pending}
        required
      >
        <option value="">{t("select")}</option>
        {value && !rows.some((v) => v.id === value) && (
          <option value={value}>
            {t("boundVersion")} {value}
          </option>
        )}
        {rows.map((v) => (
          <option key={v.id} value={v.id}>
            {v.name} · {t("revision")} {v.revision}
          </option>
        ))}
      </Choice>
      {cursor && (
        <Button type="button" variant="ghost" disabled={task.pending} onClick={() => load(cursor)}>
          {t("more")}
        </Button>
      )}
      <Link href={`/evaluations/${kind}`} className="text-sm underline">
        {t("manageVersions")}
      </Link>
    </div>
  );
}
