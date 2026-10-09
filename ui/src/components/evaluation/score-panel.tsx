"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import type { components } from "@/lib/api/generated/schema";
import { numericScore } from "@/lib/evaluation-view/matrix";

import { Field } from "./form-fields";
type S = components["schemas"];
export function ScorePanel({
  batchId,
  result,
  scoreRevision,
  rubric: historyRubric,
  reviewContext,
  onReview,
  onRescore,
  canReview = false,
  pending = false,
  onLoadMore,
}: {
  batchId?: string;
  result: { id: string; result_revision: number };
  scoreRevision: S["ScoreHistoryPage"];
  rubric?: S["RubricVersion"];
  reviewContext?: S["CurrentReviewContext"];
  onReview: (body: Omit<S["AppendHumanReview"], "request_id">) => void;
  onRescore: (body: Omit<S["RescoreCommand"], "request_id">) => void;
  canReview?: boolean;
  pending?: boolean;
  onLoadMore?: () => void;
}) {
  const t = useTranslations("evaluations");
  const [acceptedContext, setAcceptedContext] = useState(reviewContext);
  const rubric = canReview ? (acceptedContext?.rubric ?? historyRubric) : historyRubric;
  const fingerprint = (context: typeof reviewContext) =>
    JSON.stringify(
      context && [
        context.result_id,
        context.result_revision,
        context.evaluation_revision,
        context.rubric.id,
        context.applicable_dimensions,
        context.human_heads,
      ],
    );
  const needsReconciliation =
    !!reviewContext && fingerprint(acceptedContext) !== fingerprint(reviewContext);
  const [dimension, setDimension] = useState(acceptedContext?.applicable_dimensions[0] ?? "");
  const writeReady =
    !!acceptedContext &&
    !!reviewContext &&
    !needsReconciliation &&
    acceptedContext.result_id === result.id &&
    acceptedContext.applicable_dimensions.includes(dimension);
  const [value, setValue] = useState("0");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<"valid" | "not_evaluable" | "error">("valid");
  const [newRubric, setNewRubric] = useState("");
  const [judge, setJudge] = useState("");
  const [tokens, setTokens] = useState("");
  const [money, setMoney] = useState("");
  const [evidence, setEvidence] = useState<S["ResourceIdentity"][]>([]);
  const resources = [
    ...scoreRevision.items.flatMap((s) => s.score.evidence ?? []),
    ...evidence,
  ].filter(
    (resource, index, all) =>
      all.findIndex((r) => JSON.stringify(r) === JSON.stringify(resource)) === index,
  );
  const head = acceptedContext?.human_heads.find((head) => head.dimension === dimension);
  const prior = head ? { id: head.id, score: head } : undefined;
  return (
    <aside className="min-w-0 space-y-5 rounded-md border p-4">
      <h2 className="text-lg font-semibold">
        {t("scoreDetails")} · {t("revision")} {scoreRevision.evaluation_revision}
      </h2>
      {(["rule", "model", "human"] as const).map((source) => (
        <section key={source} data-source={source} className="space-y-2">
          <h3 className="font-semibold">
            {t(
              source === "rule" ? "ruleScores" : source === "model" ? "modelScores" : "humanScores",
            )}
          </h3>
          {!scoreRevision.items.some((row) => row.score.source === source) && (
            <p>{t("missingScore")}</p>
          )}
          {scoreRevision.items
            .filter((row) => row.score.source === source)
            .map((row) => (
              <article key={row.id} className="space-y-1 border-l-2 pl-3 text-sm break-words">
                {scoreRevision.invalidations?.some(
                  (invalidation) =>
                    invalidation.source_set_id === row.source_set_id && row.source_set_id,
                ) && (
                  <p role="status" className="font-semibold">
                    {t("invalidatedScore")}
                  </p>
                )}
                <p>
                  {row.score.dimension}:{" "}
                  {typeof row.score.value === "boolean"
                    ? t(row.score.value ? "rulePassed" : "ruleFailed")
                    : (numericScore(row.score.value) ?? t("missingScore"))}{" "}
                  · {t("revision")} {row.evaluation_revision}
                </p>
                <p>
                  {t("rubricVersion")}: {row.score.rubric_revision}
                </p>
                <p>{row.score.reason}</p>
                <p>
                  {row.author} · {row.timestamp}
                </p>
                {row.supersedes_id && (
                  <p>
                    {t("supersedes")}: {row.supersedes_id}
                  </p>
                )}
                <a
                  className="underline"
                  href={`/runs/${encodeURIComponent(row.run_id)}?${new URLSearchParams({ result: row.result_id, evaluation_run: row.run_id, result_revision: String(row.result_revision), score_revision: String(row.evaluation_revision), score_run_revision: String(row.run_revision), ...(batchId ? { batch: batchId } : {}) })}`}
                >
                  {t("openRun")} · {row.run_revision}
                </a>
                {row.score.evidence?.map((resource) => (
                  <p key={JSON.stringify(resource)}>
                    {resource.resource_kind}: {resource.resource_id} · {resource.resource_version}
                  </p>
                ))}
                {row.score.recording && (
                  <p>
                    {t("simulatedEvidence")} · {t("revision")} {row.score.recording.revision} ·{" "}
                    {row.score.recording.consumed}/{row.score.recording.total} · {t("mismatches")}{" "}
                    {row.score.recording.mismatches}
                  </p>
                )}
              </article>
            ))}
        </section>
      ))}
      {scoreRevision.next_cursor && onLoadMore && (
        <Button disabled={pending} variant="outline" onClick={onLoadMore}>
          {t("loadMore")}
        </Button>
      )}
      {rubric && (
        <details>
          <summary>
            {rubric.name} · {t("rubricVersion")} {rubric.id}
          </summary>
          {(rubric.dimensions ?? [])
            .filter(
              (d) =>
                !canReview ||
                !acceptedContext ||
                acceptedContext.applicable_dimensions.includes(d.id),
            )
            .map((d) => (
              <div key={d.id}>
                <h4>{d.name}</h4>
                <ol start={0}>
                  {d.anchors.map((anchor, index) => (
                    <li key={index}>
                      {index}: {anchor}
                    </li>
                  ))}
                </ol>
              </div>
            ))}
        </details>
      )}
      {canReview && needsReconciliation && (
        <div role="alert">
          <p>{t("reviewContextChanged")}</p>
          <p>
            {t("rubricVersion")}: {reviewContext?.rubric.id} · {t("revision")}{" "}
            {reviewContext?.evaluation_revision}
          </p>
          <ul>
            {reviewContext?.human_heads.map((head) => (
              <li key={head.id}>
                {head.dimension}: {head.value ?? t("missingScore")}
              </li>
            ))}
          </ul>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setAcceptedContext(reviewContext);
              if (!reviewContext?.applicable_dimensions.includes(dimension))
                setDimension(reviewContext?.applicable_dimensions[0] ?? "");
            }}
          >
            {t("reconcileReview")}
          </Button>
        </div>
      )}
      {canReview && rubric && (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!writeReady) return;
            onReview({
              rubric_version: rubric.id,
              expected_result_revision: acceptedContext!.result_revision,
              expected_revision: acceptedContext!.evaluation_revision,
              scores: [
                {
                  dimension,
                  status,
                  value: status === "valid" ? Number(value) : null,
                  reason,
                  supersedes_id: prior?.id,
                  evidence,
                },
              ],
            });
          }}
        >
          <h3>{t("humanReview")}</h3>
          <label className="block">
            {t("dimension")}
            <select
              className="bg-background block w-full rounded border p-2"
              value={dimension}
              onChange={(event) => setDimension(event.target.value)}
            >
              {(rubric.dimensions ?? [])
                .filter((d) => acceptedContext?.applicable_dimensions.includes(d.id))
                .map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
            </select>
          </label>
          <p>
            {t("originalValue")}:{" "}
            {typeof prior?.score.value === "number" ? prior.score.value : t("missingScore")}
          </p>
          <label className="block">
            {t("scoreState")}
            <select
              className="bg-background block w-full rounded border p-2"
              value={status}
              onChange={(event) => setStatus(event.target.value as typeof status)}
            >
              <option value="valid">{t("matrixScored")}</option>
              <option value="not_evaluable">{t("missingScore")}</option>
              <option value="error">{t("failed")}</option>
            </select>
          </label>
          {status === "valid" && (
            <Field label={t("newValue")} value={value} onChange={setValue} type="number" required />
          )}
          <Field label={t("reason")} value={reason} onChange={setReason} required />
          {resources.map((resource) => {
            const key = JSON.stringify(resource);
            return (
              <label key={key} className="block break-words">
                <input
                  type="checkbox"
                  checked={evidence.some((item) => JSON.stringify(item) === key)}
                  onChange={(event) =>
                    setEvidence((old) =>
                      event.target.checked
                        ? [...old, resource]
                        : old.filter((v) => JSON.stringify(v) !== key),
                    )
                  }
                />
                {resource.resource_kind}: {resource.resource_id}
              </label>
            );
          })}
          <Button
            disabled={
              !writeReady ||
              pending ||
              (status === "valid" &&
                (!Number.isInteger(Number(value)) || Number(value) < 0 || Number(value) > 4))
            }
          >
            {t("submitReview")}
          </Button>
        </form>
      )}
      {canReview && (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!writeReady) return;
            onRescore({
              rubric_version: newRubric,
              judge_config_version: judge,
              expected_evaluation_revision: acceptedContext!.evaluation_revision,
              expected_result_revision: acceptedContext!.result_revision,
              token_budget: Number(tokens),
              money_budget: money || null,
            });
          }}
        >
          <h3>{t("rescore")}</h3>
          <p>{t("additionalBudgetNotice")}</p>
          <Field
            label={t("publishedRubricId")}
            value={newRubric}
            onChange={setNewRubric}
            required
          />
          <Field label={t("publishedJudgeId")} value={judge} onChange={setJudge} required />
          <Field
            label={t("tokenBudget")}
            value={tokens}
            onChange={setTokens}
            type="number"
            required
          />
          <Field label={t("moneyBudget")} value={money} onChange={setMoney} />
          <Button
            disabled={
              !writeReady || pending || !Number.isSafeInteger(Number(tokens)) || Number(tokens) <= 0
            }
          >
            {t("submitRescore")}
          </Button>
        </form>
      )}
    </aside>
  );
}
