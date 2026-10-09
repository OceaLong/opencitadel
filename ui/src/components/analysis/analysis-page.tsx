"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { EvaluationError, useEvaluationTask } from "@/components/evaluation/evaluation-boundary";
import { Button } from "@/components/ui/button";

import { useAnalysisSource } from "@/hooks/use-analysis-source";
import {
  type NavigationContext,
  navigationOwner,
  readNavigationContext,
  saveNavigationContext,
} from "@/lib/analysis-view/navigation-context";
import { refreshIntent } from "@/lib/analysis-view/refresh-intent";
import {
  acceptedQuery,
  queryParams,
  restoreScroll,
  withScroll,
} from "@/lib/analysis-view/return-context";
import { emptySelection } from "@/lib/analysis-view/selection";
import { analysisApi } from "@/lib/api/execution-analysis";
import { ApiError } from "@/lib/api/fetch";
import type {
  AnalysisPreference,
  AnalysisRunPage,
  AnalysisSummary,
  SummaryQuery,
} from "@/lib/api/types/execution-analysis";

import type { AnalysisAccess } from "./analysis-boundary";
import { RetainedEvaluationSeries } from "./evaluation-series";
import { ExportPanel } from "./export-panel";
import { FilterBar } from "./filter-bar";
import { MetricFacts } from "./metric-facts";
import { MetricSummary } from "./metric-summary";
import { RunCharts } from "./run-charts";
import { RunTable } from "./run-table";
export function AnalysisPage({ access }: { access: AnalysisAccess }) {
  const t = useTranslations("analysis");
  const router = useRouter();
  const search = useSearchParams();
  const task = useEvaluationTask(access);
  const preferenceTask = useEvaluationTask(access);
  const preferenceWriteTask = useEvaluationTask({ ...access, deny: undefined });
  const [draft, setDraft] = useState(() => acceptedQuery(new URLSearchParams(search.toString())));
  const [query, setQuery] = useState(draft);
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [page, setPage] = useState<AnalysisRunPage | null>(null);
  const [selection, setSelection] = useState(emptySelection);
  const [returnSearch, setReturnSearch] = useState(new URLSearchParams());
  const refresh = refreshIntent(returnSearch.size ? returnSearch : search);
  const [restoreFailed, setRestoreFailed] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const retrySave = useRef<(() => void) | null>(null);
  const owner = navigationOwner(access);
  const saveContext = (
    context: NavigationContext,
    accept: (href: string) => void,
  ): string | null => {
    try {
      const href = saveNavigationContext(context, owner);
      setSaveFailed(false);
      retrySave.current = null;
      accept(href);
      return href;
    } catch {
      setSaveFailed(true);
      retrySave.current = () => {
        saveContext(context, accept);
      };
      return null;
    }
  };
  const [cursor, setCursor] = useState<string | undefined>(search.get("cursor") || undefined);
  const [captureUnavailable, setCaptureUnavailable] = useState(false);
  const [preference, setPreference] = useState<AnalysisPreference | null>(null);
  const [zone, setZone] = useState("");
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [baseline, setBaseline] = useState(search.get("baseline") ?? "");
  const load = (
    next: SummaryQuery,
    pageCursor?: string,
    keepSelection = false,
    restored?: NavigationContext,
  ) => {
    const keptSelection = restored?.selection ?? (keepSelection ? selection : emptySelection());
    task.cancel();
    setSummary(null);
    setPage(null);
    setSelection(keptSelection);
    setCursor(pageCursor);
    setCaptureUnavailable(false);
    setQuery(next);
    void task.run(
      async (o) => {
        try {
          const summary = await analysisApi.summary(next, o);
          const page = await analysisApi.runs(
            { ...next, watermark: summary.watermark, cursor: pageCursor },
            o,
          );
          return { summary, page };
        } catch (error) {
          if (
            !o.signal?.aborted &&
            next.watermark &&
            error instanceof ApiError &&
            ([404, 410].includes(error.code) ||
              error.msg === "analysis_refresh_required" ||
              (error.data !== null &&
                typeof error.data === "object" &&
                "code" in error.data &&
                error.data.code === "analysis_refresh_required"))
          )
            setCaptureUnavailable(true);
          throw error;
        }
      },
      (value) => {
        const accepted = {
          ...next,
          watermark: value.summary.watermark,
          timezone: value.summary.timezone,
        };
        setQuery(accepted);
        const url = queryParams(accepted);
        if (pageCursor) url.set("cursor", pageCursor);
        if (keepSelection)
          for (const key of [
            "scroll",
            "baseline",
            "stratum",
            "selected_result",
            "refresh_comparison",
            "expected_revision",
          ]) {
            const saved = (restored?.params ?? returnSearch).get(key);
            if (saved !== null) url.set(key, saved);
          }
        if (refresh) {
          url.set("refresh_comparison", refresh.id);
          url.set("expected_revision", String(refresh.revision));
        }
        setReturnSearch(url);
        saveContext(
          { path: "/analysis", params: url, selection: keptSelection, anchor: restored?.anchor },
          (href) => router.replace(href, { scroll: false }),
        );
        setSummary(value.summary);
        setPage(value.page);
      },
    );
  };
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) {
        try {
          const restored = readNavigationContext(
            "/analysis",
            new URLSearchParams(search.toString()),
            owner,
          );
          const params = restored?.params ?? new URLSearchParams(search.toString());
          const next = acceptedQuery(params);
          setDraft(next);
          setBaseline(params.get("baseline") ?? "");
          setReturnSearch(params);
          load(next, params.get("cursor") || undefined, true, restored ?? undefined);
        } catch {
          setRestoreFailed(true);
        }
        void preferenceTask.run(
          (o) => analysisApi.getPreferences(o),
          (p) => {
            setPreference(p);
            setZone(p.timezone ?? "");
          },
        );
      }
    });
    return () => {
      cancelled = true;
    }; // this owner mounts once per authenticated scope
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!page) return;
    restoreScroll(returnSearch);
  }, [page, returnSearch]);
  useAnalysisSource({
    value: summary,
    workspaceId: access.workspaceId,
    read: (options) => analysisApi.summary({ ...query, watermark: summary!.watermark }, options),
    invalidate: () => load({ ...query, watermark: summary!.watermark }, cursor, true),
  });
  const apply = (next: SummaryQuery) => {
    setRestoreFailed(false);
    setSaveFailed(false);
    setReturnSearch(new URLSearchParams());
    next = { ...next, watermark: undefined };
    const url = queryParams(next);
    if (refresh) {
      url.set("refresh_comparison", refresh.id);
      url.set("expected_revision", String(refresh.revision));
    }
    router.replace(`/analysis?${url}`);
    load(next);
  };
  const returnTo = (anchor?: string, values: Record<string, string> = {}) => {
    const url = queryParams(query);
    if (cursor) url.set("cursor", cursor);
    if (baseline) url.set("baseline", baseline);
    if (refresh) {
      url.set("refresh_comparison", refresh.id);
      url.set("expected_revision", String(refresh.revision));
    }
    for (const [key, value] of Object.entries(values)) url.set(key, value);
    const back = new URL(withScroll(`/analysis?${url}`, anchor), window.location.origin);
    return saveContext(
      { path: "/analysis", params: back.searchParams, selection, anchor },
      () => {},
    );
  };
  const openRun = (id: string) => {
    const back = returnTo(`run-${id}`);
    if (back)
      router.push(`/runs/${encodeURIComponent(id)}?analysis_return=${encodeURIComponent(back)}`);
  };
  const comparison = () => {
    const payload = {
      mode: selection.mode,
      run_ids: selection.runIds,
      excluded_run_ids: selection.excludedIds,
      detail_run_ids: selection.details,
      baseline_configuration: baseline || null,
      filters: query.filters,
      grain: query.grain ?? "day",
      timezone: summary?.timezone ?? query.timezone ?? "UTC",
    };
    void task.run(
      (o) =>
        refresh
          ? analysisApi.refreshComparison(
              refresh.id,
              {
                ...payload,
                expected_revision: refresh.revision,
                request_id: task.requestId("refreshComparison", { ...payload, ...refresh }),
              },
              o,
            )
          : analysisApi.createComparison(
              { ...payload, request_id: task.requestId("comparison", payload) },
              o,
            ),
      (c) => router.push(`/analysis/comparisons/${c.comparison_id}?revision=${c.revision}`),
    );
  };
  const nextPage = () => {
    if (!summary || !page?.next_cursor) return;
    const cursor = page.next_cursor;
    void task.run(
      (o) => analysisApi.runs({ ...query, watermark: summary.watermark, cursor }, o),
      (next) => {
        const url = queryParams(query);
        url.set("cursor", cursor);
        if (baseline) url.set("baseline", baseline);
        if (refresh) {
          url.set("refresh_comparison", refresh.id);
          url.set("expected_revision", String(refresh.revision));
        }
        saveContext({ path: "/analysis", params: url, selection }, (href) => {
          setReturnSearch(new URLSearchParams());
          setCursor(cursor);
          setPage(next);
          router.replace(href, { scroll: false });
        });
      },
    );
  };
  return (
    <>
      <h1 className="text-2xl font-semibold">{t("title")}</h1>
      {restoreFailed && (
        <div role="alert">
          <p>{t("returnContextUnavailable")}</p>
          <Button onClick={() => apply(acceptedQuery(new URLSearchParams()))}>
            {t("startNewAnalysis")}
          </Button>
        </div>
      )}
      {saveFailed && (
        <div role="alert">
          <p>{t("returnContextSaveFailed")}</p>
          <Button onClick={() => retrySave.current?.()}>{t("retrySaveContext")}</Button>
        </div>
      )}
      {refresh && <p>{t("refreshSelectionHint", { revision: refresh.revision })}</p>}
      <FilterBar
        value={draft}
        onChange={setDraft}
        onApply={() => apply(draft)}
        pending={task.pending}
      />
      <details>
        <summary>{t("workspaceTimezone")}</summary>
        <div className="flex flex-wrap gap-2 py-3">
          <input
            aria-label={t("workspaceTimezone")}
            className="bg-background rounded border p-2"
            value={zone}
            onChange={(e) => setZone(e.target.value)}
          />
          <Button
            disabled={!access.canWritePreferences || !preference || preferenceWriteTask.pending}
            onClick={() => {
              const body = { expected_revision: preference!.revision, timezone: zone || null };
              void preferenceWriteTask.run(
                (o) =>
                  analysisApi.updatePreferences(
                    { ...body, request_id: preferenceWriteTask.requestId("timezone", body) },
                    o,
                  ),
                setPreference,
              );
            }}
          >
            {t("saveTimezone")}
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              void preferenceTask.run((o) => analysisApi.getPreferences(o), setPreference)
            }
          >
            {t("rereadRevision")}
          </Button>
        </div>
        <p>{t("timezoneHint")}</p>
        <EvaluationError error={preferenceTask.error} />
        <EvaluationError error={preferenceWriteTask.error} />
      </details>
      {captureUnavailable ? (
        <div role="alert">
          <p>{t("captureUnavailable")}</p>
          <Button onClick={() => apply({ ...query, watermark: undefined })}>
            {t("newCapture")}
          </Button>
        </div>
      ) : (
        <EvaluationError error={task.error} refresh={() => load(query, cursor, true)} />
      )}
      {task.pending && <p role="status">{t("loading")}</p>}
      {summary && (
        <div
          key={summary.watermark}
          className="space-y-6"
          data-native-view="analysis"
          data-native-ready={
            !task.pending &&
            !task.error &&
            page?.watermark === summary.watermark &&
            page.availability === "available"
          }
          data-public-scope={access.workspaceId}
          data-public-watermark={summary.watermark}
          data-public-metric-version={summary.metric_version}
        >
          <p className="text-sm">
            {t("captured")}: {summary.metrics.captured_at} · {t("coverage")}:{" "}
            {typeof summary.metrics.coverage === "string" ? summary.metrics.coverage : t("unknown")}
          </p>
          <RunCharts
            summary={summary}
            context={{
              start: query.filters?.start ?? "",
              end: query.filters?.end ?? "",
              watermark: summary.watermark,
              timezone: summary.timezone,
              metricVersion: summary.metric_version,
            }}
            onRun={openRun}
            onTool={(tool) => {
              const next = { ...query, filters: { ...query.filters, tool } };
              setDraft(next);
              apply(next);
            }}
          />
          <MetricFacts metrics={summary.metrics} />
          <MetricSummary metrics={summary.metrics} baseline={baseline || null} />
          <RetainedEvaluationSeries
            metrics={summary.metrics}
            returnTo={returnTo}
            returnSearch={returnSearch}
          />
          {page?.availability === "retained_data_unavailable" ? (
            <p>{t("retainedUnavailable")}</p>
          ) : (
            <RunTable
              rows={page?.items ?? []}
              timezone={summary.timezone}
              selection={selection}
              onChange={(next) => {
                retrySave.current = null;
                setSaveFailed(false);
                setSelection(next);
              }}
              onOpen={openRun}
              onNext={nextPage}
              canNext={!!page?.next_cursor}
              pending={task.pending}
            />
          )}
          <div className="flex flex-wrap items-end gap-3">
            <label>
              {t("baseline")}
              <input
                className="bg-background block rounded border p-2"
                value={baseline}
                onChange={(e) => setBaseline(e.target.value)}
              />
            </label>
            <Button
              disabled={
                !access.canManage ||
                task.pending ||
                (selection.mode === "explicit" && !selection.runIds.length)
              }
              onClick={comparison}
            >
              {refresh ? t("publishRefresh") : t("createComparison")}
            </Button>
            <label>
              {t("exportFormat")}
              <select
                className="bg-background block rounded border p-2"
                value={format}
                onChange={(e) => setFormat(e.target.value as "csv" | "json")}
              >
                <option value="csv" translate="no">
                  CSV
                </option>
                <option value="json" translate="no">
                  JSON
                </option>
              </select>
            </label>
          </div>
          <ExportPanel
            key={JSON.stringify([query, selection, format])}
            access={access}
            source={{
              source_kind: "filter",
              request_id: "pending",
              format,
              selection: {
                mode: selection.mode,
                run_ids: selection.runIds,
                excluded_run_ids: selection.excludedIds,
                filters: query.filters,
                grain: query.grain ?? "day",
                timezone: summary.timezone,
              },
            }}
            description={`${t("freshExportHint")} ${query.filters?.start} – ${query.filters?.end} · ${summary.timezone}`}
          />
        </div>
      )}
    </>
  );
}
