"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";

import { histogramPresentation } from "@/lib/analysis-view/chart-contracts";
import { buildTrendGroups } from "@/lib/analysis-view/trend-dataset";
import type { AnalysisSummary } from "@/lib/api/types/execution-analysis";

export type ChartContext = {
  start: string;
  end: string;
  watermark: string;
  timezone: string;
  metricVersion: string;
};
export function ChartSource({
  context,
  children,
}: {
  context: ChartContext;
  children: React.ReactNode;
}) {
  const t = useTranslations("analysis");
  return (
    <>
      <p className="text-muted-foreground text-sm break-words">
        {context.start} – {context.end} · {context.timezone} · {context.metricVersion}
      </p>
      <details className="min-w-0">
        <summary className="cursor-pointer py-2">{t("viewData")}</summary>
        <p className="text-xs break-all">
          {t("watermark")}: {context.watermark}
        </p>
        <div className="max-h-96 overflow-auto">{children}</div>
      </details>
    </>
  );
}
export function RunCharts({
  summary,
  context,
  onTool,
  onRun,
}: {
  summary: AnalysisSummary;
  context: ChartContext;
  onTool: (name: string) => void;
  onRun: (id: string) => void;
}) {
  const t = useTranslations("analysis");
  const locale = useLocale();
  const [toolMode, setToolMode] = useState<"count" | "rate">("count");
  const [showAll, setShowAll] = useState(false);
  const [groupPage, setGroupPage] = useState(0);
  const groups = buildTrendGroups(summary.metrics.series ?? [], summary.grain);
  const visible = groups.slice(groupPage * 5, groupPage * 5 + 5);
  const charts = summary.metrics.charts;
  const latencyMaximum = Math.max(
    1,
    ...groups.flatMap((group) => group.rows.flatMap((row) => [row.p50 ?? 0, row.p95 ?? 0])),
  );

  const compactTime = new Intl.DateTimeFormat(locale, {
    timeZone: summary.timezone,
    month: "short",
    day: "numeric",
    ...(summary.grain === "hour" ? { hour: "2-digit" as const } : {}),
  });
  const fullTime = new Intl.DateTimeFormat(locale, {
    timeZone: summary.timezone,
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "shortOffset",
  });
  const formatTime = (stamp: number) => compactTime.format(stamp);
  const unknown = (v: number | null | undefined) => (v == null ? t("unknown") : String(v));
  const tools = [...(charts?.tools.items ?? [])].sort((a, b) =>
    toolMode === "count"
      ? b.errors - a.errors || (a.tool_name ?? "").localeCompare(b.tool_name ?? "")
      : (b.error_rate.value ?? -1) - (a.error_rate.value ?? -1) ||
        (a.tool_name ?? "").localeCompare(b.tool_name ?? ""),
  );
  const shownTools = showAll ? tools : tools.slice(0, 10);
  const bars = shownTools.map((row) => ({
    ...row,
    label: row.tool_name ?? t("unknownTool"),
    value:
      toolMode === "count"
        ? row.errors
        : row.error_rate.value == null
          ? null
          : row.error_rate.value * 100,
  }));
  const bins =
    charts?.latency.edges_ms.slice(0, -1).map((edge, i) => ({
      label: `${edge / 1000}–${charts.latency.edges_ms[i + 1] / 1000}`,
      count: charts.latency.bin_counts[i],
    })) ?? [];
  if (charts)
    bins.push({
      label: `≥${charts.latency.overflow.lower_ms / 1000}`,
      count: charts.latency.overflow.count,
    });
  return (
    <section className="min-w-0 space-y-8">
      {groups.length > 5 && (
        <div className="flex flex-wrap items-center gap-2">
          <p>{t("fiveGroups")}</p>
          <Button disabled={groupPage === 0} onClick={() => setGroupPage((p) => p - 1)}>
            {t("previous")}
          </Button>
          <Button
            disabled={(groupPage + 1) * 5 >= groups.length}
            onClick={() => setGroupPage((p) => p + 1)}
          >
            {t("next")}
          </Button>
        </div>
      )}
      <div className="grid min-w-0 gap-6 lg:grid-cols-2">
        {(["success", "latency"] as const).map((kind) => (
          <section key={kind} className="min-w-0 space-y-3">
            <h2 className="text-lg font-semibold">
              {t(kind === "success" ? "successTrend" : "latencyTrend")}
            </h2>
            {visible.length === 0 && <p>{t("noObservations")}</p>}
            {visible.map((group, index) => (
              <div key={group.key} className="min-w-0">
                <p
                  key={JSON.stringify([context.watermark, group.key, group.observed])}
                  {...{ elementtiming: "analysis-values" }}
                  data-native-content="analysis-values"
                  className="text-sm break-words"
                >
                  {group.identity.configuration_revision ?? t("unassignedConfiguration")} ·{" "}
                  {group.identity.family} · {group.identity.execution_mode} ·{" "}
                  {group.identity.purpose} · {t("sampleSymbol")}={group.observed}
                </p>
                <div
                  className="h-52"
                  role="img"
                  aria-label={t(kind === "success" ? "successTrend" : "latencyTrend")}
                >
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart
                      data={group.rows}
                      margin={{ left: 0, right: 22, top: 12, bottom: 8 }}
                    >
                      <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                      <XAxis
                        dataKey="timestamp"
                        type="number"
                        domain={["dataMin", "dataMax"]}
                        tickFormatter={formatTime}
                        ticks={[
                          ...new Set(
                            [group.rows[0]?.timestamp, group.rows.at(-1)?.timestamp].filter(
                              (stamp): stamp is number => stamp !== undefined,
                            ),
                          ),
                        ]}
                        interval={0}
                        padding={{ left: 8, right: 8 }}
                        minTickGap={12}
                        tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                        stroke="var(--muted-foreground)"
                      />
                      <YAxis
                        dataKey={group.presentation === "discrete" ? "value" : undefined}
                        domain={kind === "success" ? [0, 100] : [0, latencyMaximum]}
                        tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                        stroke="var(--muted-foreground)"
                        unit={kind === "success" ? "%" : "s"}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "var(--background)",
                          color: "var(--foreground)",
                          borderColor: "var(--border)",
                        }}
                        labelFormatter={(label) =>
                          `${new Date(Number(label)).toISOString()} · ${summary.timezone}`
                        }
                      />
                      {(kind === "success"
                        ? (["success"] as const)
                        : (["p50", "p95"] as const)
                      ).map((key, i) =>
                        group.presentation === "discrete" ? (
                          <Scatter
                            key={key}
                            name={key}
                            data={group.rows.flatMap((row) =>
                              row.observed && row[key] !== null
                                ? [{ timestamp: row.timestamp, value: row[key] }]
                                : [],
                            )}
                            fill={`var(--chart-${index + 1})`}
                            isAnimationActive={false}
                          />
                        ) : (
                          <Line
                            key={key}
                            dataKey={key}
                            name={key}
                            stroke={`var(--chart-${index + 1})`}
                            strokeDasharray={i ? "6 3" : undefined}
                            strokeWidth={2}
                            connectNulls={false}
                            dot={{ r: 3 }}
                            isAnimationActive={false}
                          />
                        ),
                      )}
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
                {group.presentation === "discrete" && <p className="text-sm">{t("sparseTrend")}</p>}
              </div>
            ))}
            <ChartSource context={context}>
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    <th>{t("configuration")}</th>
                    <th>{t("bucket")}</th>
                    <th>{kind === "success" ? "%" : "p50 / p95 (s)"}</th>
                    <th>{t("coverage")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.flatMap((group) =>
                    group.rows.map((row) => (
                      <tr key={`${group.key}/${row.timestamp}`}>
                        <td className="break-all">
                          {group.identity.configuration_revision ?? t("unassignedConfiguration")}
                        </td>
                        <td>
                          <time dateTime={new Date(row.timestamp).toISOString()}>
                            {new Date(row.timestamp).toISOString()} ·{" "}
                            {fullTime.format(row.timestamp)}
                          </time>
                        </td>
                        <td>
                          {!row.observed
                            ? t("noEvents")
                            : kind === "success"
                              ? unknown(row.success)
                              : `${unknown(row.p50)} / ${unknown(row.p95)}`}
                        </td>
                        <td>
                          {row.source &&
                            (() => {
                              const m =
                                row.source.metrics[
                                  kind === "success" ? "success_rate" : "latency_p50"
                                ];
                              return m
                                ? `n=${m.sample_count}; ${t("missing")}=${m.missing_count}; ${t("excluded")}=${m.excluded_count}; ${m.numerator ?? "—"}/${m.denominator ?? "—"}`
                                : "—";
                            })()}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </ChartSource>
          </section>
        ))}
      </div>
      {!charts ? (
        <p role="status">{t("retainedUnavailable")}</p>
      ) : (
        <div className="grid min-w-0 gap-6 lg:grid-cols-2">
          <section className="min-w-0 space-y-3">
            <h2 className="text-lg font-semibold">{t("toolErrors")}</h2>
            <div className="flex flex-wrap gap-2">
              <Button
                variant={toolMode === "count" ? "default" : "outline"}
                onClick={() => setToolMode("count")}
              >
                {t("failureCount")}
              </Button>
              <Button
                variant={toolMode === "rate" ? "default" : "outline"}
                onClick={() => setToolMode("rate")}
              >
                {t("failureRate")}
              </Button>
              <Button variant="outline" onClick={() => setShowAll(!showAll)}>
                {t(showAll ? "topTen" : "showAll")}
              </Button>
            </div>
            {charts.tools.availability !== "available" ? (
              <p>{t("retainedUnavailable")}</p>
            ) : (
              <>
                <div
                  style={{ height: Math.max(180, bars.length * 38) }}
                  role="img"
                  aria-label={t("toolErrors")}
                >
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={bars} layout="vertical" margin={{ left: 4, right: 30 }}>
                      <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                      <XAxis
                        stroke="var(--muted-foreground)"
                        tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                        type="number"
                        domain={toolMode === "rate" ? [0, 100] : [0, "auto"]}
                        allowDecimals={toolMode === "rate"}
                        unit={toolMode === "rate" ? "%" : undefined}
                      />
                      <YAxis
                        stroke="var(--muted-foreground)"
                        tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                        type="category"
                        dataKey="label"
                        width={115}
                        tickFormatter={(s) => (s.length > 12 ? `${s.slice(0, 11)}…` : s)}
                      />
                      <Tooltip />
                      <Bar
                        dataKey="value"
                        onClick={(_, index) => {
                          const name = bars[index]?.tool_name;
                          if (name) onTool(name);
                        }}
                        fill="var(--chart-1)"
                        isAnimationActive={false}
                        label={{ position: "right", fill: "var(--foreground)", fontSize: 12 }}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <ChartSource context={context}>
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr>
                        <th>{t("tool")}</th>
                        <th>{t("failureCount")}</th>
                        <th>{t("failureRate")}</th>
                        <th>{t("coverage")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tools.map((row) => (
                        <tr key={row.tool_name ?? "unknown"}>
                          <td className="max-w-64 break-words">
                            {row.tool_name ? (
                              <button
                                className="text-primary underline"
                                onClick={() => onTool(row.tool_name!)}
                              >
                                {row.tool_name}
                              </button>
                            ) : (
                              t("unknownTool")
                            )}
                          </td>
                          <td>{row.errors}</td>
                          <td>
                            {row.error_rate.value == null
                              ? t("unknown")
                              : `${(row.error_rate.value * 100).toFixed(1)}%`}{" "}
                            ({row.errors}/{row.terminal})
                          </td>
                          <td>
                            {row.terminal < 20 && <span>{t("smallSample")} · </span>}
                            {t("executionBusiness")}: {row.execution_errors}/{row.business_errors} ·{" "}
                            {t("unknownDeferredCancelled")}: {row.unknown}/{row.deferred}/
                            {row.cancelled}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ChartSource>
              </>
            )}
          </section>
          <section className="min-w-0 space-y-3">
            <h2 className="text-lg font-semibold">{t("latencyDistribution")}</h2>
            <p>
              {t("sampleSymbol")}={charts.latency.p50.sample_count} · {t("missing")}=
              {charts.latency.p50.missing_count} · {t("excluded")}=
              {charts.latency.p50.excluded_count}
            </p>
            <p>
              {t("p50Label")}=
              {unknown(charts.latency.p50.value == null ? null : charts.latency.p50.value / 1000)}{" "}
              <span translate="no">s</span>· {t("p95Label")}=
              {unknown(charts.latency.p95.value == null ? null : charts.latency.p95.value / 1000)}{" "}
              <span translate="no">s</span>
            </p>
            {histogramPresentation(charts.latency.p50.sample_count) === "histogram" ? (
              <div className="h-72" role="img" aria-label={t("latencyDistribution")}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={bins} barCategoryGap={1} margin={{ bottom: 10, right: 12 }}>
                    <XAxis
                      stroke="var(--muted-foreground)"
                      tick={{ fill: "var(--muted-foreground)" }}
                      dataKey="label"
                      unit="s"
                      minTickGap={25}
                    />
                    <YAxis
                      stroke="var(--muted-foreground)"
                      tick={{ fill: "var(--muted-foreground)" }}
                      allowDecimals={false}
                    />
                    <Tooltip />
                    <Bar dataKey="count" fill="var(--chart-1)" isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p>{t("sparseHistogram")}</p>
            )}
            <ChartSource context={context}>
              {histogramPresentation(charts.latency.p50.sample_count) === "exact" ? (
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr>
                      <th translate="no">Run</th>
                      <th translate="no">s</th>
                    </tr>
                  </thead>
                  <tbody>
                    {charts.latency.samples.map((row) => (
                      <tr key={row.run_id}>
                        <td>
                          <button
                            className="text-primary break-all underline"
                            onClick={() => onRun(row.run_id)}
                          >
                            {row.run_id}
                          </button>
                        </td>
                        <td>{row.duration_ms / 1000}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr>
                      <th>
                        {t("range")} <span translate="no">(s)</span>
                      </th>
                      <th translate="no">n</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bins.map((row) => (
                      <tr key={row.label}>
                        <td>{row.label}</td>
                        <td>{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p>
                {charts.latency.scheme} · {charts.latency.edge_convention} · {t("maximum")}:{" "}
                {unknown(
                  charts.latency.overflow.maximum_ms == null
                    ? null
                    : charts.latency.overflow.maximum_ms / 1000,
                )}{" "}
                <span translate="no">s</span>
              </p>
            </ChartSource>
          </section>
        </div>
      )}
    </section>
  );
}
