"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";
import { evaluationStatusKey } from "@/lib/evaluation-view/status";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
export function BatchList({ access }: { access: EvaluationAccess }) {
  const t = useTranslations("evaluations");
  const { run, pending, error } = useEvaluationTask(access);
  const [page, setPage] = useState<components["schemas"]["BatchListPage"] | null>(null);
  useEffect(() => {
    void run((o) => evaluationApi.batches(o), setPage);
  }, [run]);
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">{t("batches")}</h2>
      <Link className="underline" href="/evaluations/reviews">
        {t("reviewQueue")}
      </Link>
      <EvaluationError error={error} />
      {pending && <p>{t("loading")}</p>}
      <ul>
        {page?.items.map((batch) => (
          <li className="border-b py-3" key={batch.id}>
            <Link className="underline" href={`/evaluations/batches/${batch.id}`}>
              {batch.name}
            </Link>
            <p className="text-muted-foreground text-sm">
              {t(evaluationStatusKey(batch.status))} · {t("revision")} {batch.evaluation_revision} ·{" "}
              {batch.created_at}
            </p>
          </li>
        ))}
      </ul>
      {page?.next_cursor && (
        <Button
          variant="outline"
          disabled={pending}
          onClick={() => void run((o) => evaluationApi.batches(o, page.next_cursor!), setPage)}
        >
          {t("loadMore")}
        </Button>
      )}
    </section>
  );
}
