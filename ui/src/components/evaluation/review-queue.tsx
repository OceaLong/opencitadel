"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";

export function ReviewQueue({ access }: { access: EvaluationAccess }) {
  const t = useTranslations("evaluations");
  const [page, setPage] = useState<components["schemas"]["ReviewPage"] | null>(null);
  const { run, pending, error } = useEvaluationTask(access);
  useEffect(() => {
    void run((options) => evaluationApi.reviews(options), setPage);
  }, [run]);
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold">{t("reviewQueue")}</h1>
      <EvaluationError
        error={error}
        refresh={() => void run((options) => evaluationApi.reviews(options), setPage)}
      />
      {pending && <p role="status">{t("loading")}</p>}
      {page && !page.items.length && <p>{t("reviewEmpty")}</p>}
      <ul className="space-y-3">
        {page?.items.map((item) => (
          <li key={item.result_id} className="rounded-md border p-3 break-words">
            <Link
              className="underline"
              href={`/evaluations/batches/${encodeURIComponent(item.batch_id)}?${new URLSearchParams({ result: item.result_id, run: item.run_id, score_revision: String(item.evaluation_revision), result_revision: String(item.result_revision) })}`}
            >
              {item.result_id}
            </Link>
            <p>
              {t("revision")} {item.evaluation_revision} · {t("reviewPending")} ·{" "}
              {item.received_dimensions.length}/{item.required_dimensions.length}
            </p>
            <p>
              {t("requiredDimensions")}: {item.required_dimensions.join(", ")}
            </p>
          </li>
        ))}
      </ul>
      {page?.next_cursor && (
        <Button
          disabled={pending}
          variant="outline"
          onClick={() =>
            void run((options) => evaluationApi.reviews(options, page.next_cursor!), setPage)
          }
        >
          {t("loadMore")}
        </Button>
      )}
    </section>
  );
}
