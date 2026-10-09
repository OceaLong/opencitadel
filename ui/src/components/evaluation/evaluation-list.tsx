"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { type Collection, evaluationApi } from "@/lib/api/evaluations";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Field } from "./form-fields";
export function EvaluationList({
  kind,
  access,
}: {
  kind: Collection | "datasets";
  access: EvaluationAccess;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [items, setItems] = useState<{ id: string; name: string; revision: number }[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [name, setName] = useState("");
  const load = useCallback(
    () =>
      void runTask(
        async (o) =>
          kind === "datasets"
            ? { items: await evaluationApi.datasets(o), next_cursor: null }
            : evaluationApi.list(kind, o),
        (page) => {
          setItems(page.items);
          setCursor(page.next_cursor ?? null);
        },
        true,
      ),
    [kind, runTask],
  );
  useEffect(load, [load]);
  if (task.error === "denied") return <EvaluationError error="denied" />;
  return (
    <>
      <Link href="/evaluations" className="text-sm underline">
        {t("back")}
      </Link>
      <h1 className="text-xl font-semibold">{t(kind)}</h1>
      <EvaluationError error={task.error} refresh={load} />
      {access.canManage &&
        (kind === "datasets" ? (
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              const body = { name, expected_revision: 0 };
              void runTask(
                (o) =>
                  evaluationApi.createDataset(
                    { ...body, request_id: task.requestId("createDataset", body) },
                    o,
                  ),
                (v) => {
                  setItems((old) => [v, ...old]);
                  setName("");
                },
              );
            }}
          >
            <Field label={t("name")} value={name} onChange={setName} required />
            <Button disabled={task.pending}>{t("createDataset")}</Button>
          </form>
        ) : (
          <Link
            href={`/evaluations/${kind}/new`}
            className="inline-flex min-h-9 items-center text-sm underline"
          >
            {t("createVersion")}
          </Link>
        ))}
      <ul className="divide-y rounded-md border">
        {items.map((item) => (
          <li key={item.id}>
            <Link
              href={`/evaluations/${kind}/${item.id}`}
              className="block min-h-9 p-3 break-words"
            >
              {item.name}{" "}
              <span className="text-muted-foreground text-sm">
                · {t("revision")} {item.revision}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {!items.length && <p role="status">{t(task.pending ? "loading" : "empty")}</p>}
      {cursor && kind !== "datasets" && (
        <Button
          variant="outline"
          disabled={task.pending}
          onClick={() =>
            void runTask(
              (o) => evaluationApi.list(kind, o, false, cursor),
              (page) => {
                setItems((old) => [...old, ...page.items]);
                setCursor(page.next_cursor ?? null);
              },
            )
          }
        >
          {t("more")}
        </Button>
      )}
    </>
  );
}
export function EvaluationHome() {
  const t = useTranslations("evaluations");
  return (
    <>
      <h1 className="text-xl font-semibold">{t("title")}</h1>
      <p className="text-muted-foreground text-sm">{t("intro")}</p>
      <nav className="grid gap-2 sm:grid-cols-2">
        {(["datasets", "configs", "rubrics", "recordings", "environments", "suites"] as const).map(
          (kind) => (
            <Link
              key={kind}
              href={`/evaluations/${kind}`}
              className="hover:bg-accent rounded-md border p-4 font-medium"
            >
              {t(kind)}
            </Link>
          ),
        )}
      </nav>
    </>
  );
}
