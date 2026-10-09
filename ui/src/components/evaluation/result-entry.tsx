"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
/** A selected evaluation result belongs to exactly one scored Run. */
export function EvaluationResultEntry({ runId }: { runId: string }) {
  const search = useSearchParams();
  const t = useTranslations("evaluations");
  const batch = search.get("batch"),
    result = search.get("result"),
    revision = search.get("score_revision"),
    run = search.get("evaluation_run"),
    cut = search.get("score_run_revision");
  if (!batch || !result || run !== runId || !revision || !/^\d+$/.test(revision)) return null;
  const query = new URLSearchParams({ result, run, score_revision: revision });
  const resultRevision = search.get("result_revision");
  if (resultRevision) query.set("result_revision", resultRevision);
  return (
    <Link className="underline" href={`/evaluations/batches/${encodeURIComponent(batch)}?${query}`}>
      {t("result")} · {t("revision")} {revision} · {t("scoredRunRevision")} {cut}
    </Link>
  );
}
