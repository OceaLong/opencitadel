"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { EvaluationCharts } from "@/components/analysis/analysis-charts";
import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";
import { evaluationStatusKey } from "@/lib/evaluation-view/status";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { useEvaluationFeed } from "./evaluation-feed";
import { type MatrixResult, type MatrixRow, ResultMatrix } from "./result-matrix";
import { ScorePanel } from "./score-panel";
type S = components["schemas"];
export function BatchOverview({ id, access }: { id: string; access: EvaluationAccess }) {
  const t = useTranslations("evaluations");
  const search = useSearchParams();
  const rawRevision = search.get("score_revision");
  const pin = rawRevision !== null && /^\d+$/.test(rawRevision) ? Number(rawRevision) : undefined;
  const [batch, setBatch] = useState<S["BatchView"] | null>(null);
  const [environments, setEnvironments] = useState<S["BatchEnvironmentPage"] | null>(null);
  const [summary, setSummary] = useState<S["SummaryPage"] | null>(null);
  const [rubricFilter, setRubricFilter] = useState("");
  const [rubricDraft, setRubricDraft] = useState("");
  const [source, setSource] = useState("model");
  const [dimension, setDimension] = useState("correctness");
  const [dimensionDraft, setDimensionDraft] = useState("correctness");
  const [selected, setSelected] = useState<string | null>(search.get("result"));
  const [scores, setScores] = useState<S["ScoreHistoryPage"] | null>(null);
  const [rubric, setRubric] = useState<S["RubricVersion"] | undefined>();
  const [reviewContext, setReviewContext] = useState<S["CurrentReviewContext"]>();
  const loadedExtent = useRef(0);
  const filterKey = JSON.stringify([id, source, dimension, rubricFilter, pin]);
  const priorFilter = useRef(filterKey);
  const [receipt, setReceipt] = useState<S["ReviewReceipt"] | null>(null);
  const [retryBatch, setRetryBatch] = useState<string | null>(null);
  const read = useEvaluationTask(access);
  const detail = useEvaluationTask(access);
  const command = useEvaluationTask(access);
  const { run: readRun, cancel: cancelRead } = read;
  const { run: detailRun } = detail;
  const { run: commandRun } = command;
  const query = {
    source,
    dimension,
    rubric_id: rubricFilter || undefined,
    evaluation_revision: pin?.toString(),
    result_id: selected ?? undefined,
  };
  const readBusy = useRef(false);
  const refreshAgain = useRef(false);
  const refreshLatest = useRef<() => void>(() => {});
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const refresh = useCallback(() => {
    if (!active.current) return;
    if (readBusy.current) {
      refreshAgain.current = true;
      return;
    }
    readBusy.current = true;
    void readRun(
      async (o) => {
        const batch = await evaluationApi.batch(id, o);
        const environments = await evaluationApi.batchEnvironments(id, o);
        const query = {
          source,
          dimension,
          rubric_id: rubricFilter || undefined,
          evaluation_revision: pin?.toString(),
          result_id: selected ?? undefined,
        };
        let next = await evaluationApi.summary(id, o, query);
        const snapshot = next.snapshot_id;
        while (next.items.length < loadedExtent.current && next.next_cursor) {
          const page = await evaluationApi.summary(id, o, { ...query, cursor: next.next_cursor });
          if (page.snapshot_id !== snapshot) throw new Error("summary_refresh_required");
          next = { ...page, points: next.points, items: [...next.items, ...page.items] };
        }
        const current =
          selected && pin === undefined
            ? await evaluationApi.reviewContext(selected, o)
            : undefined;
        return { batch, environments, summary: next, current };
      },
      (value) => {
        setBatch(value.batch);
        setEnvironments(value.environments);
        loadedExtent.current = value.summary.items.length;
        setSummary(value.summary);
        setReviewContext(value.current);
        if (value.current) setRubric(value.current.rubric);
      },
      false,
    ).finally(() => {
      readBusy.current = false;
      if (refreshAgain.current && active.current) {
        refreshAgain.current = false;
        refreshLatest.current();
      }
    });
  }, [readRun, id, source, dimension, rubricFilter, pin, selected]);
  useEffect(() => {
    refreshLatest.current = refresh;
    // Clear the old query snapshot before replacing it; never append across cuts.

    if (priorFilter.current !== filterKey) {
      priorFilter.current = filterKey;
      loadedExtent.current = 0;
      // Clear a different query immediately; background refresh keeps the old coherent snapshot.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSummary(null);
    }
    cancelRead();
    refresh();
  }, [refresh, cancelRead, filterKey]);
  useEvaluationFeed(access, id, refresh);
  const scoreCut = pin ?? summary?.evaluation_revision;
  useEffect(() => {
    if (!selected || scoreCut === undefined) return;
    void detailRun(
      async (o) => {
        const history = await evaluationApi.scores(selected, o, scoreCut);
        const version =
          pin !== undefined && summary?.rubric_id
            ? await evaluationApi.version("rubrics", summary.rubric_id, o)
            : undefined;
        return {
          history,
          rubric: version && "dimensions" in version ? (version as S["RubricVersion"]) : undefined,
        };
      },
      (v) => {
        setScores(v.history);
        if (pin !== undefined) setRubric(v.rubric);
      },
      true,
    );
  }, [selected, scoreCut, summary?.rubric_id, detailRun, pin]);
  useEffect(() => {
    if (!receipt || ["accepted", "failed", "completed", "cancelled"].includes(receipt.status))
      return;
    const timer = setTimeout(
      () =>
        void commandRun(
          (o) => evaluationApi.reviewCommand(receipt.id, o),
          (value) => {
            setReceipt(value);
            refresh();
          },
        ),
      1500,
    );
    return () => clearTimeout(timer);
  }, [receipt, commandRun, refresh]);
  const matrixRows = new Map<string, MatrixRow>();
  const configs = new Map<string, string>();
  for (const row of summary?.items ?? []) {
    configs.set(row.config_id, row.config_label);
    let group = matrixRows.get(row.case_id);
    if (!group) {
      group = { id: row.case_id, label: row.case_label, results: [] };
      matrixRows.set(row.case_id, group);
    }
    const result: MatrixResult = {
      id: row.id,
      configId: row.config_id,
      repetition: row.repetition,
      attempt: row.attempt,
      runId: row.score_run_id ?? row.run_id,
      evaluationRevision: summary!.evaluation_revision,
      resultRevision: row.score_result_revision ?? row.result_revision,
      executionStatus: row.execution_status,
      scoringStatus: row.scoring_status,
      value: typeof row.value === "boolean" ? Number(row.value) : row.value,
    };
    group.results = [...group.results, result];
  }
  const selectedRow =
    summary?.selected_result?.id === selected
      ? summary.selected_result
      : summary?.items.find((row) => row.id === selected);
  const changed = (value: S["ReviewReceipt"]) => {
    setReceipt(value);
    refresh();
  };
  return (
    <>
      <Link className="underline" href="/evaluations">
        {t("back")}
      </Link>
      <h1 className="text-2xl font-semibold">
        {t("batch")} <span className="text-base break-all">{id}</span>
      </h1>
      <EvaluationError error={read.error ?? detail.error ?? command.error} refresh={refresh} />
      {batch && (
        <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            [t("executionState"), t(evaluationStatusKey(batch.status))],
            [
              t("automaticScoring"),
              Object.entries(summary?.scoring_counts ?? {})
                .map(([key, value]) => `${t(evaluationStatusKey(key))}: ${value}`)
                .join(" · "),
            ],
            [t("reviewState"), t(evaluationStatusKey(batch.review_status ?? ""))],
            [t("cleanupState"), t(evaluationStatusKey(batch.cleanup_status ?? ""))],
          ].map(([label, value]) => (
            <div key={label} className="rounded border p-3">
              <dt className="text-muted-foreground text-sm">{label}</dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {environments && environments.items.length > 0 && (
        <section aria-label={t("environmentLeases")} className="space-y-3">
          <h2 className="text-lg font-semibold">{t("environmentLeases")}</h2>
          <p className="text-muted-foreground text-sm">{t("environmentHistoryLimit")}</p>
          {environments.items.map((lease) => (
            <article
              key={lease.id}
              data-lease-id={lease.id}
              className="space-y-1 rounded border p-3 break-words"
            >
              <p className="break-all">
                {t("environmentLease")}: {lease.id}
              </p>
              <p>
                {t("environmentCurrentState")}: {t(evaluationStatusKey(lease.state))} ·{" "}
                {t(lease.reusable ? "environmentReusable" : "environmentNotReusable")}
              </p>
              <p>
                {t("environmentGeneration")}: {lease.generation} · {t("revision")}: {lease.revision}
              </p>
              <p className="break-all">
                {t("environmentCase")}: {lease.case_id} · {t("environmentConfiguration")}:{" "}
                {lease.config_version} · {t("environmentRepeat")}: {lease.repeat}
              </p>
              <p className="break-all">
                {t("environmentVersion")}: {lease.environment_version}
              </p>
              {Object.entries(lease.prior_failed_operations).length > 0 ? (
                <div>
                  <p>{t("environmentPriorQuarantine")}</p>
                  <ul>
                    {Object.entries(lease.prior_failed_operations).map(([phase, count]) => (
                      <li key={phase}>
                        {t(environmentPhaseKey(phase))}: {count}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p>{t("environmentNoRetainedFailure")}</p>
              )}
            </article>
          ))}
          {environments.next_cursor && (
            <Button
              variant="outline"
              disabled={read.pending}
              onClick={() =>
                void read.run(
                  (o) => evaluationApi.batchEnvironments(id, o, environments.next_cursor!),
                  (page) =>
                    setEnvironments({ ...page, items: [...environments.items, ...page.items] }),
                )
              }
            >
              {t("loadMore")}
            </Button>
          )}
        </section>
      )}
      {access.canRun && (
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={command.pending}
            variant="outline"
            onClick={() =>
              void command.run(
                (o) =>
                  evaluationApi.cancelBatch(id, { request_id: command.requestId("cancel", id) }, o),
                setBatch,
              )
            }
          >
            {t("cancelBatch")}
          </Button>
          <Button
            disabled={command.pending}
            variant="outline"
            onClick={() =>
              void command.run(
                (o) =>
                  evaluationApi.retryBatch(
                    id,
                    { request_id: command.requestId("retry", [id, batch?.revision]) },
                    o,
                  ),
                (v) => setRetryBatch(v.id),
              )
            }
          >
            {t("retryFailed")}
          </Button>
          {retryBatch && (
            <Link className="underline" href={`/evaluations/batches/${retryBatch}`}>
              {t("newBatch")}
            </Link>
          )}
        </div>
      )}
      {receipt && (
        <div role="status" className="rounded border p-3">
          {t("commandStatus")}: {t(evaluationStatusKey(receipt.status))} · {t("revision")}{" "}
          {receipt.evaluation_revision}
          {receipt.judge_run_id && (
            <p>
              {t("judgeRun")}: {receipt.judge_run_id}
            </p>
          )}
          {receipt.error && <p>{receipt.error}</p>}
          {access.canReview &&
            pin === undefined &&
            receipt.kind === "rescore" &&
            receipt.judge_run_id &&
            !["completed", "failed", "cancelled"].includes(receipt.status) && (
              <Button
                variant="outline"
                disabled={command.pending}
                onClick={() =>
                  void commandRun(
                    (o) =>
                      evaluationApi.cancelJudge(
                        receipt.result_id,
                        {
                          expected_revision:
                            summary?.evaluation_revision ?? receipt.evaluation_revision,
                          expected_result_revision: receipt.result_revision,
                          judge_run_id: receipt.judge_run_id!,
                          request_id: command.requestId("cancel-judge", [
                            receipt.id,
                            receipt.judge_run_id,
                          ]),
                        },
                        o,
                      ),
                    changed,
                  )
                }
              >
                {t("cancelJudge")}
              </Button>
            )}
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        <label>
          {t("scoreSource")}
          <select
            className="bg-background ml-2 rounded border p-2"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            <option value="rule">{t("ruleScores")}</option>
            <option value="model">{t("modelScores")}</option>
            <option value="human">{t("humanScores")}</option>
          </select>
        </label>
        <label>
          {t("dimension")}
          <input
            className="ml-2 rounded border p-2"
            value={dimensionDraft}
            onChange={(e) => setDimensionDraft(e.target.value)}
            onBlur={() => setDimension(dimensionDraft.trim())}
            onKeyDown={(event) => {
              if (event.key === "Enter") setDimension(dimensionDraft.trim());
            }}
          />
        </label>
        <label>
          {t("rubricVersion")}
          <input
            className="ml-2 rounded border p-2"
            value={rubricDraft}
            placeholder={summary?.rubric_id}
            onChange={(event) => setRubricDraft(event.target.value)}
            onBlur={() => setRubricFilter(rubricDraft.trim())}
            onKeyDown={(event) => {
              if (event.key === "Enter") setRubricFilter(rubricDraft.trim());
            }}
          />
        </label>
        <Button variant="outline" onClick={refresh}>
          {t("refresh")}
        </Button>
      </div>
      {summary && (
        <>
          <p className="text-muted-foreground text-sm">
            {t("revision")} {summary.evaluation_revision} · {t("costCaptured")}{" "}
            {summary.usage_watermark}
            {pin !== undefined && ` · ${t("pinnedScore")}`}
          </p>
          <ResultMatrix
            renderIdentity={{
              scopeId: access.workspaceId,
              batchId: batch!.id,
              snapshotId: summary.snapshot_id,
              revision: summary.evaluation_revision,
              ready: !read.pending && !read.error && !!batch,
            }}
            key={filterKey}
            rows={[...matrixRows.values()]}
            configs={[...configs].map(([id, label]) => ({ id, label }))}
            selection={selected}
            onSelectResult={(result) => setSelected(result.id)}
            onLoadMore={
              summary.next_cursor && !read.pending
                ? () =>
                    void readRun(
                      (o) =>
                        evaluationApi.summary(id, o, { ...query, cursor: summary.next_cursor! }),
                      (next) =>
                        setSummary((old) => {
                          if (!old || old.snapshot_id !== next.snapshot_id) return old;
                          const items = [...old.items, ...next.items];
                          loadedExtent.current = items.length;
                          return { ...next, items, points: old.points };
                        }),
                    )
                : undefined
            }
          />
          <EvaluationCharts
            distributionMetadata={summary.distribution_metadata}
            qualityCostMetadata={summary.quality_cost_metadata}
            onSelectResult={(resultId, revision) => {
              // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- Reinitialize selected result and transient review state at the pinned score cut.
              window.location.href = `/evaluations/batches/${id}?result=${encodeURIComponent(resultId)}&score_revision=${revision}`;
            }}
            points={summary.points}
            labels={Object.fromEntries(configs)}
            source={source}
            dimension={dimension}
            revision={summary.evaluation_revision}
            usageWatermark={summary.usage_watermark}
            reviewStatus={batch?.review_status ?? ""}
          />
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {(["subject_usage", "judge_usage"] as const).map((kind) => {
              const usage = summary[kind];
              return (
                <div className="rounded border p-3" key={kind}>
                  <dt>{t(kind === "subject_usage" ? "subjectCost" : "judgeCost")}</dt>
                  <dd>
                    {t("currencyUsd")} {usage?.money ?? t("unknownCost")} · {t("tokenUnit")}{" "}
                    {usage?.tokens ?? t("unknownCost")}
                  </dd>
                  <dd>
                    {t("coverage")}: {usage?.money_known ?? 0}/{usage?.calls ?? 0} ·{" "}
                    {t("unresolvedCalls")}: {usage?.unresolved ?? 0}
                  </dd>
                </div>
              );
            })}
          </dl>
          <div className="space-y-1 text-sm">
            {summary.allocations.map((a, index) => (
              <p key={a.intent_id ?? index}>
                {t(a.kind === "original" ? "originalBudget" : "additionalBudget")}: {a.token_budget}{" "}
                {t("tokenUnit")} · {t("currencyUsd")} {a.money_budget ?? t("unknownCost")} ·{" "}
                {t("authorizedBy")} {a.authorizer}
              </p>
            ))}
          </div>
        </>
      )}
      {pin !== undefined && selected && (
        <Link
          className="underline"
          href={`/evaluations/batches/${id}?result=${encodeURIComponent(selected)}`}
        >
          {t("reviewCurrent")}
        </Link>
      )}
      {selectedRow && (
        <div className="flex flex-wrap gap-3">
          {selectedRow.attempts?.map((attempt) => (
            <Link
              key={attempt.run_id}
              className="underline"
              href={`/runs/${encodeURIComponent(attempt.run_id)}?${new URLSearchParams({ batch: id, result: selectedRow.id, evaluation_run: attempt.run_id, score_revision: String(scoreCut ?? 0), score_run_revision: String(attempt.run_revision), result_revision: String(selectedRow.result_revision) })}`}
            >
              {t("openRun")} · {t("attempt")} {attempt.attempt + 1} ·{" "}
              {t(evaluationStatusKey(attempt.status))}
            </Link>
          ))}
        </div>
      )}
      {selected && scores && (!scores.items.length || scores.items[0].result_id === selected) && (
        <ScorePanel
          key={selected}
          batchId={id}
          result={{
            id: selected,
            result_revision:
              selectedRow?.result_revision ?? Number(search.get("result_revision") ?? 0),
          }}
          scoreRevision={scores}
          rubric={rubric}
          reviewContext={pin === undefined ? reviewContext : undefined}
          canReview={access.canReview && !!selectedRow && pin === undefined}
          pending={command.pending}
          onLoadMore={
            scores.next_cursor
              ? () =>
                  void detailRun(
                    (o) =>
                      evaluationApi.scores(
                        selected,
                        o,
                        scores.evaluation_revision,
                        scores.next_cursor!,
                      ),
                    (page) =>
                      setScores((old) =>
                        old ? { ...page, items: [...old.items, ...page.items] } : page,
                      ),
                  )
              : undefined
          }
          onReview={(body) =>
            void command.run(
              (o) =>
                evaluationApi.review(
                  selected,
                  { ...body, request_id: command.requestId("review", body) },
                  o,
                ),
              changed,
            )
          }
          onRescore={(body) =>
            void command.run(
              (o) =>
                evaluationApi.rescore(
                  selected,
                  { ...body, request_id: command.requestId("rescore", body) },
                  o,
                ),
              changed,
            )
          }
        />
      )}
    </>
  );
}

function environmentPhaseKey(phase: string) {
  const keys = {
    prepare: "environmentPhasePrepare",
    reset: "environmentPhaseReset",
    verify_ready: "environmentPhaseVerifyReady",
    cleanup: "environmentPhaseCleanup",
    verify_clean: "environmentPhaseVerifyClean",
  } as const;
  return keys[phase as keyof typeof keys] ?? "stateUnknown";
}
