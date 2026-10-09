"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { EvaluationError, useEvaluationTask } from "@/components/evaluation/evaluation-boundary";
import { DetailPanel } from "@/components/execution/detail-panel";
import { TraceView } from "@/components/execution/trace-view";
import { Button } from "@/components/ui/button";

import { useAnalysisSource } from "@/hooks/use-analysis-source";
import {
  type NavigationContext,
  navigationOwner,
  readNavigationContext,
  saveNavigationContext,
} from "@/lib/analysis-view/navigation-context";
import { restoreScroll, withScroll } from "@/lib/analysis-view/return-context";
import { analysisApi } from "@/lib/api/execution-analysis";
import type { ComparisonEnvelope } from "@/lib/api/types/execution-analysis";

import type { AnalysisAccess } from "./analysis-boundary";
import { ArtifactDiff } from "./artifact-diff";
import { RetainedEvaluationSeries } from "./evaluation-series";
import { ExportPanel } from "./export-panel";
import { MetricFacts } from "./metric-facts";
import { MetricSummary } from "./metric-summary";
import { RunCharts } from "./run-charts";
import { RunStatus } from "./run-facts";
type Detail = ComparisonEnvelope["details"][number];
function TraceSlot({
  access,
  comparison,
  detail,
  index,
  onPick,
  stepId,
}: {
  access: AnalysisAccess;
  comparison: ComparisonEnvelope;
  detail: Detail;
  index: number;
  onPick: (step: string) => void;
  stepId: string | null;
}) {
  const t = useTranslations("analysis");
  const step = detail.body?.steps.find((s) => s.step_id === stepId);
  return (
    <section className="min-w-0 space-y-2" data-full-trace-owner={detail.run_id}>
      <h3 className="font-medium break-all">
        {index + 1}. {detail.run_id}
      </h3>
      {detail.availability !== "available" ? (
        <p>{t("retainedUnavailable")}</p>
      ) : (
        <>
          <TraceView
            steps={detail.body?.steps ?? []}
            selection={stepId}
            onSelectStep={(id) => {
              onPick(id);
            }}
            asOf={comparison.captured_at}
            viewport={{ height: 400 }}
            exhausted
            complete
          />
          <div className="max-h-[32rem] overflow-auto">
            {step && (
              <DetailPanel
                retained={{
                  ownerKey: JSON.stringify([
                    access.ownerKey,
                    comparison.comparison_id,
                    comparison.revision,
                    detail.run_id,
                    step.step_id,
                    step.attempt_id,
                  ]),
                  workspaceId: access.workspaceId,
                  comparisonId: comparison.comparison_id,
                  revision: comparison.revision,
                  step,
                  onRevoked: access.deny ?? (() => {}),
                }}
              />
            )}
          </div>
        </>
      )}
    </section>
  );
}
export function ComparisonWorkspace({
  access,
  id,
  revision,
}: {
  access: AnalysisAccess;
  id: string;
  revision: number;
}) {
  const t = useTranslations("analysis");
  const router = useRouter();
  const search = useSearchParams();
  const pair = (key: string, params: Pick<URLSearchParams, "get"> = search): (string | null)[] => {
    try {
      const value = JSON.parse(params.get(key) ?? "null");
      if (
        Array.isArray(value) &&
        value.length === 2 &&
        value.every((v) => v === null || typeof v === "string")
      )
        return value;
    } catch {}
    return [null, null];
  };
  const task = useEvaluationTask(access);
  const owner = navigationOwner(access);
  const [returnSearch, setReturnSearch] = useState(new URLSearchParams());
  const [restoreFailed, setRestoreFailed] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const retrySave = useRef<(() => void) | null>(null);
  const saveContext = (context: NavigationContext): string | null => {
    try {
      const href = saveNavigationContext(context, owner);
      setSaveFailed(false);
      retrySave.current = null;
      return href;
    } catch {
      setSaveFailed(true);
      retrySave.current = () => {
        saveContext(context);
      };
      return null;
    }
  };
  const [value, setValue] = useState<ComparisonEnvelope | null>(null);
  const [slots, setSlots] = useState<(string | null)[]>(() => pair("slots"));
  const [steps, setSteps] = useState<(string | null)[]>(() => pair("steps"));
  const [cursor, setCursor] = useState<string | undefined>(search.get("cursor") || undefined);
  const [artifactIndices, setArtifactIndices] = useState([0, 0]);
  const [exportFormat, setExportFormat] = useState<"csv" | "json">("csv");
  const [format, setFormat] = useState<"text" | "json">("text");
  const [generation, setGeneration] = useState(0);
  const [receipt, setReceipt] = useState<number | null>(null);
  const load = (nextSlots = slots, pageCursor = cursor, keepSteps = false) => {
    task.cancel();
    setValue(null);
    if (!keepSteps) setSteps([null, null]);
    setGeneration((g) => g + 1);
    void task.run(
      (o) =>
        analysisApi.getComparison(
          id,
          {
            revision,
            limit: 50,
            detail_run_ids: nextSlots.filter((v): v is string => !!v),
            cursor: pageCursor,
          },
          o,
        ),
      (value) => {
        setCursor(pageCursor);
        setValue(value);
      },
    );
  };
  useEffect(() => {
    let stopped = false;
    queueMicrotask(() => {
      if (!stopped) {
        try {
          const restored = readNavigationContext(
            `/analysis/comparisons/${id}`,
            new URLSearchParams(search.toString()),
            owner,
          );
          const params = restored?.params ?? new URLSearchParams(search.toString());
          const nextSlots = pair("slots", params);
          setSlots(nextSlots);
          setSteps(pair("steps", params));
          setReturnSearch(params);
          load(nextSlots, params.get("cursor") || undefined, true);
        } catch {
          setRestoreFailed(true);
        }
      }
    });
    return () => {
      stopped = true;
    }; // fixed owner remounts per comparison/revision
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (value) restoreScroll(returnSearch);
  }, [value, returnSearch]);
  const returnTo = (anchor?: string, values: Record<string, string> = {}) => {
    const params = new URLSearchParams({
      revision: String(revision),
      slots: JSON.stringify(slots),
      steps: JSON.stringify(steps),
    });
    if (cursor) params.set("cursor", cursor);
    for (const [key, value] of Object.entries(values)) params.set(key, value);
    const back = new URL(
      withScroll(`/analysis/comparisons/${id}?${params}`, anchor),
      window.location.origin,
    );
    return saveContext({ path: back.pathname, params: back.searchParams, anchor });
  };
  const openRun = (run: string) => {
    const back = returnTo(`run-${run}`);
    if (back)
      router.push(`/runs/${encodeURIComponent(run)}?analysis_return=${encodeURIComponent(back)}`);
  };
  useAnalysisSource({
    value,
    workspaceId: access.workspaceId,
    read: (options) => analysisApi.getComparison(id, { revision, limit: 1 }, options),
    invalidate: () => load(),
  });
  const replaceSlot = (index: number, run: string) => {
    const next = slots.map((v, i) => (i === index ? run || null : v === run ? null : v));
    setSlots(next);
    setArtifactIndices([0, 0]);
    load(next);
  };
  const activeDetails = slots.map((run) => value?.details.find((d) => d.run_id === run));
  const selectedSteps = activeDetails.map((d, i) =>
    d?.body?.steps.find((s) => s.step_id === steps[i]),
  );
  const artifacts = selectedSteps.map((step, i) => step?.artifact_refs?.[artifactIndices[i]]);
  const align = (
    action: "confirm" | "unpair",
    left = selectedSteps[0],
    right = selectedSteps[1],
  ) => {
    if (!value || !left || !right) return;
    const payload = {
      revision,
      expected_revision: value.alignment_revision,
      edits: [
        {
          left_run_id: left.run_id,
          right_run_id: right.run_id,
          left_step_id: left.step_id,
          right_step_id: right.step_id,
          left_attempt_id: left.attempt_id,
          right_attempt_id: right.attempt_id,
          action,
        },
      ],
    };
    setReceipt(null);
    void task.run(
      (o) => analysisApi.align(id, { ...payload, request_id: task.requestId("align", payload) }, o),
      (v) => {
        setReceipt(v.accepted_alignment_revision ?? null);
        load(slots, cursor, true);
      },
    );
  };
  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{t("comparisonTitle")}</h1>
        <Button variant="outline" onClick={() => router.push("/analysis")}>
          {t("backToAnalysis")}
        </Button>
        <Button variant="outline" disabled={task.pending} onClick={() => load()}>
          {t("rereadRevision")}
        </Button>
      </div>
      <p className="break-all">
        {id} · {t("membershipRevision")}: {revision}
      </p>
      {restoreFailed && (
        <div role="alert">
          <p>{t("returnContextUnavailable")}</p>
          <Button
            onClick={() => {
              setRestoreFailed(false);
              setSlots([null, null]);
              setSteps([null, null]);
              setCursor(undefined);
              setReturnSearch(new URLSearchParams());
              load([null, null], undefined);
              router.replace(`/analysis/comparisons/${id}?revision=${revision}`);
            }}
          >
            {t("rereadRevision")}
          </Button>
        </div>
      )}
      {saveFailed && (
        <div role="alert">
          <p>{t("returnContextSaveFailed")}</p>
          <Button onClick={() => retrySave.current?.()}>{t("retrySaveContext")}</Button>
        </div>
      )}
      <EvaluationError error={task.error} />
      {task.pending && <p role="status">{t("loading")}</p>}
      {value && (
        <div key={`${id}:${revision}:${generation}`} className="space-y-6">
          <p>
            {t("captured")}: {value.captured_at} · {value.timezone} · {t("visibleMembers")}:{" "}
            {value.member_count} · {t("alignmentRevision")}: {value.alignment_revision}
          </p>
          {value.coverage_changed && <p role="status">{t("coverageChanged")}</p>}
          {receipt !== null && (
            <p>
              {t("acceptedAlignment")}: {receipt}
            </p>
          )}
          {value.context && (
            <Button
              variant="outline"
              onClick={() =>
                router.push(
                  `/analysis?${new URLSearchParams({ refresh_comparison: id, expected_revision: String(revision), filters: JSON.stringify({ ...value.context!.filters, start: value.context!.start, end: value.context!.end }), grain: value.context!.grain, timezone: value.timezone })}`,
                )
              }
            >
              {t("refreshSelection")}
            </Button>
          )}
          {value.context && (
            <RunCharts
              summary={{
                metrics: value.metrics,
                grain: value.context.grain,
                timezone: value.timezone,
                watermark: `${id}:${revision}`,
                metric_version: value.metric_version,
              }}
              context={{
                start: value.context.start,
                end: value.context.end,
                timezone: value.timezone,
                watermark: `${id}:${revision}`,
                metricVersion: value.metric_version,
              }}
              onTool={(tool) =>
                router.push(
                  `/analysis?${new URLSearchParams({ filters: JSON.stringify({ ...value.context!.filters, start: value.context!.start, end: value.context!.end, tool }), grain: value.context!.grain, timezone: value.timezone })}`,
                )
              }
              onRun={openRun}
            />
          )}
          <MetricFacts metrics={value.metrics} />
          <MetricSummary metrics={value.metrics} baseline={value.baseline_configuration} />
          <RetainedEvaluationSeries
            metrics={value.metrics}
            returnTo={returnTo}
            returnSearch={returnSearch}
          />
          <section className="space-y-3">
            <h2 className="text-lg font-semibold">{t("retainedDetails")}</h2>
            <p>{t("twoTraces")}</p>
            <div className="grid min-w-0 gap-5 lg:grid-cols-2">
              {slots.map((run, index) => (
                <div key={index} className="min-w-0 space-y-3">
                  <label>
                    {t(index === 0 ? "leftRun" : "rightRun")}
                    <select
                      className="bg-background block w-full min-w-0 rounded border p-2"
                      value={run ?? ""}
                      onChange={(e) => replaceSlot(index, e.target.value)}
                    >
                      <option value="">{t("none")}</option>
                      {value.context?.detail_run_ids.map((id) => (
                        <option key={id} value={id}>
                          {id}
                        </option>
                      ))}
                    </select>
                  </label>
                  {activeDetails[index] && (
                    <TraceSlot
                      key={`${index}:${run}:${generation}`}
                      access={access}
                      comparison={value}
                      detail={activeDetails[index]!}
                      index={index}
                      stepId={steps[index]}
                      onPick={(step) =>
                        setSteps((old) => old.map((v, i) => (i === index ? step : v)))
                      }
                    />
                  )}
                </div>
              ))}
            </div>
            <div className="flex gap-2">
              <Button
                disabled={
                  !access.canManage || !selectedSteps[0] || !selectedSteps[1] || task.pending
                }
                onClick={() => align("confirm")}
              >
                {t("confirmPair")}
              </Button>
              <Button
                variant="outline"
                disabled={
                  !access.canManage || !selectedSteps[0] || !selectedSteps[1] || task.pending
                }
                onClick={() => align("unpair")}
              >
                {t("unpair")}
              </Button>
            </div>
            <div className="max-h-80 overflow-auto">
              <table className="w-full min-w-[36rem] text-left text-sm [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2">
                <thead>
                  <tr>
                    <th>{t("stepPair")}</th>
                    <th>{t("status")}</th>
                    <th>{t("evidence")}</th>
                    <th>{t("authorTime")}</th>
                  </tr>
                </thead>
                <tbody>
                  {value.suggestions.map((row, i) => (
                    <tr key={i} className="border-b">
                      <td className="break-all">
                        {row.left.step_id} / {row.right?.step_id ?? t("none")}
                      </td>
                      <td>
                        {t(
                          row.status === "suggested"
                            ? "suggested"
                            : row.status === "confirmed"
                              ? "confirmed"
                              : "unmatched",
                        )}
                      </td>
                      <td className="max-w-80 break-words">
                        {row.provenance} · {row.reason} · {row.algorithm_version}
                      </td>
                      <td>
                        {row.author} {row.created_at}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          {selectedSteps[0] && selectedSteps[1] && (
            <section className="space-y-3">
              <div className="flex flex-wrap gap-3">
                {selectedSteps.map((step, i) => (
                  <label key={i}>
                    {t(i === 0 ? "before" : "after")}
                    <select
                      className="bg-background block max-w-full rounded border p-2"
                      value={artifactIndices[i]}
                      onChange={(e) =>
                        setArtifactIndices((old) =>
                          old.map((v, index) => (i === index ? Number(e.target.value) : v)),
                        )
                      }
                    >
                      {step?.artifact_refs?.map((a, index) => (
                        <option key={`${a.artifact_id}:${a.version}`} value={index}>
                          {a.artifact_id} ·{a.version}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                <select
                  aria-label={t("diffFormat")}
                  value={format}
                  onChange={(e) => setFormat(e.target.value as "text" | "json")}
                >
                  <option value="text">{t("textFormat")}</option>
                  <option value="json" translate="no">
                    JSON
                  </option>
                </select>
              </div>
              {artifacts[0] && artifacts[1] && (
                <ArtifactDiff
                  key={JSON.stringify([steps, artifacts, format, generation])}
                  access={access}
                  comparisonId={id}
                  selection={{
                    revision,
                    format,
                    left: {
                      run_id: selectedSteps[0].run_id,
                      step_id: selectedSteps[0].step_id,
                      artifact_id: artifacts[0].artifact_id,
                      version: artifacts[0].version,
                    },
                    right: {
                      run_id: selectedSteps[1].run_id,
                      step_id: selectedSteps[1].step_id,
                      artifact_id: artifacts[1].artifact_id,
                      version: artifacts[1].version,
                    },
                  }}
                />
              )}
            </section>
          )}
          <section className="min-w-0">
            <h2 className="text-lg font-semibold">{t("runs")}</h2>
            <div className="overflow-auto">
              <table className="w-full min-w-[36rem] text-left text-sm [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2">
                <thead>
                  <tr>
                    <th translate="no">Run</th>
                    <th>{t("status")}</th>
                    <th>{t("family")}</th>
                    <th>{t("configuration")}</th>
                  </tr>
                </thead>
                <tbody>
                  {value.members.map((m) => (
                    <tr key={m.run_id} id={`run-${m.run_id}`}>
                      <td className="break-all">
                        <button
                          className="text-primary underline"
                          onClick={() => openRun(m.run_id)}
                        >
                          {m.run_id}
                        </button>
                      </td>
                      <td>
                        <RunStatus status={m.status} />
                      </td>
                      <td>{m.family ?? t("unknown")}</td>
                      <td className="break-all">{m.admission_configuration_id ?? t("unknown")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Button
              variant="outline"
              disabled={!value.next_cursor || task.pending}
              onClick={() => load(slots, value.next_cursor ?? undefined)}
            >
              {t("next")}
            </Button>
          </section>
          <label className="block">
            {t("exportFormat")}{" "}
            <select
              className="bg-background rounded border p-2"
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value as "csv" | "json")}
            >
              <option value="csv" translate="no">
                CSV
              </option>
              <option value="json" translate="no">
                JSON
              </option>
            </select>
          </label>
          <ExportPanel
            access={access}
            source={{
              source_kind: "comparison",
              request_id: "pending",
              comparison_id: id,
              revision,
              format: exportFormat,
            }}
            description={`${t("membershipRevision")} ${revision} · ${value.timezone} · ${value.captured_at}`}
          />
        </div>
      )}
    </>
  );
}
