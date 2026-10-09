"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  CartesianGrid,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";

import { type EvaluationPoint, evaluationSeries } from "@/lib/analysis-view/chart-contracts";
import { evaluationStatusKey } from "@/lib/evaluation-view/status";

type SeriesMetadata = {
  sample_count: number;
  missing_count: number;
  excluded_count: number;
  grain: string;
  timezone: string;
  metric_version: string;
};

export function EvaluationCharts({
  points,
  labels,
  source,
  dimension,
  revision,
  usageWatermark,
  reviewStatus,
  distributionMetadata,
  qualityCostMetadata,
  onSelectResult,
}: {
  points: readonly EvaluationPoint[];
  labels: Readonly<Record<string, string>>;
  source: string;
  dimension: string;
  revision: number;
  usageWatermark: string;
  reviewStatus: string;
  onSelectResult?: (resultId: string, scoreRevision: number) => void;
  qualityCostMetadata?: SeriesMetadata;
  distributionMetadata?: SeriesMetadata;
}) {
  const t = useTranslations("evaluations");
  const series = evaluationSeries(points);
  const [dataOffset, setDataOffset] = useState(0);
  const scoreMax = source === "rule" ? 1 : 4;
  return (
    <section className="grid min-w-0 gap-6 lg:grid-cols-2">
      <div className="min-w-0 space-y-2">
        <h2 className="text-lg font-semibold">{t("scoreDistribution")}</h2>
        <SeriesCoverage metadata={distributionMetadata} />
        <p className="text-muted-foreground text-sm">
          {t(source === "rule" ? "ruleScores" : source === "model" ? "modelScores" : "humanScores")}{" "}
          · {dimension} · {t("revision")} {revision} · {t("reviewState")}:{" "}
          {t(evaluationStatusKey(reviewStatus))}
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {series.groups.map((group, index) => (
            <span key={group.config} className="max-w-full break-words">
              {index + 1}: {labels[group.config] ?? group.config}
            </span>
          ))}
        </div>
        {series.groups.some((group) => group.values.length > 0) ? (
          <div className="h-72" role="img" aria-label={t("scoreDistribution")}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ left: 0, right: 20, top: 15, bottom: 20 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  stroke="var(--muted-foreground)"
                  tick={{ fill: "var(--muted-foreground)" }}
                  dataKey="x"
                  type="number"
                  domain={[-0.5, series.groups.length - 0.5]}
                  ticks={series.groups.map((_, i) => i)}
                  tickFormatter={(index) => String(Number(index) + 1)}
                />
                <YAxis
                  stroke="var(--muted-foreground)"
                  tick={{ fill: "var(--muted-foreground)" }}
                  dataKey="y"
                  type="number"
                  domain={[0, scoreMax]}
                />
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="bg-background rounded border p-2 text-sm">
                        {payload[0].payload.label}: {payload[0].payload.y}
                      </div>
                    ) : null
                  }
                />
                <Scatter
                  isAnimationActive={false}
                  name={dimension}
                  data={series.groups.flatMap((group, index) =>
                    group.box
                      ? [
                          {
                            x: index,
                            y: group.box.median,
                            label: labels[group.config] ?? group.config,
                          },
                        ]
                      : [],
                  )}
                  fill="var(--foreground)"
                />
                {series.groups.flatMap((group, index) =>
                  group.box
                    ? [
                        <ReferenceArea
                          key={`${group.config}-box`}
                          x1={index - 0.2}
                          x2={index + 0.2}
                          y1={group.box.q1}
                          y2={group.box.q3}
                          fill="var(--chart-1)"
                          fillOpacity={0.25}
                          stroke="var(--chart-1)"
                        />,
                        <ReferenceLine
                          key={`${group.config}-range`}
                          segment={[
                            { x: index, y: group.box.min },
                            { x: index, y: group.box.max },
                          ]}
                          stroke="var(--foreground)"
                        />,
                        <ReferenceLine
                          key={`${group.config}-median`}
                          segment={[
                            { x: index - 0.2, y: group.box.median },
                            { x: index + 0.2, y: group.box.median },
                          ]}
                          stroke="var(--foreground)"
                        />,
                      ]
                    : [
                        <Scatter
                          isAnimationActive={false}
                          key={group.config}
                          name={labels[group.config] ?? group.config}
                          data={group.values.map((value) => ({
                            x: index,
                            y: value,
                            label: labels[group.config] ?? group.config,
                          }))}
                          fill="var(--chart-1)"
                        />,
                      ],
                )}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p>{t("missingScore")}</p>
        )}
        <ul className="text-sm">
          {series.groups.map((group, index) => (
            <li key={group.config} className="break-words">
              {index + 1}. {labels[group.config] ?? group.config}: {t("sampleCount")}=
              {group.values.length}/{group.total} · {t("missingScore")} {group.missing} ·{" "}
              {t("invalidatedScore")} {group.excluded}
            </li>
          ))}
        </ul>
        <details>
          <summary className="cursor-pointer underline">{t("viewData")}</summary>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th>{t("configs")}</th>
                  <th>{t("sampleCount")}</th>
                  <th>{t("caseMeanValues")}</th>
                </tr>
              </thead>
              <tbody>
                {series.groups.map((group) => (
                  <tr key={group.config}>
                    <th className="break-words">{labels[group.config] ?? group.config}</th>
                    <td>{group.values.length}</td>
                    <td>{group.values.join(", ") || t("missingScore")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
      <div className="min-w-0 space-y-2">
        <h2 className="text-lg font-semibold">{t("qualityCost")}</h2>
        <SeriesCoverage metadata={qualityCostMetadata} />
        <p className="text-muted-foreground text-sm">
          {t("costCaptured")}: {usageWatermark} · {t("currencyUsd")} · {series.complete.length}/
          {points.length}
        </p>
        {series.scatter ? (
          <div className="h-72" role="img" aria-label={t("qualityCost")}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ left: 8, right: 24, top: 18, bottom: 32 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  stroke="var(--muted-foreground)"
                  tick={{ fill: "var(--muted-foreground)" }}
                  dataKey="cost"
                  type="number"
                  name="USD"
                  domain={[0, "auto"]}
                  label={{
                    value: t("currencyUsd"),
                    position: "insideBottom",
                    offset: -18,
                    fill: "var(--muted-foreground)",
                  }}
                />
                <YAxis
                  stroke="var(--muted-foreground)"
                  tick={{ fill: "var(--muted-foreground)" }}
                  dataKey="value"
                  type="number"
                  name={dimension}
                  domain={[0, scoreMax]}
                  label={{
                    value: dimension,
                    angle: -90,
                    position: "insideLeft",
                    fill: "var(--muted-foreground)",
                  }}
                />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} />
                <Scatter
                  isAnimationActive={false}
                  name={dimension}
                  onClick={(point) => onSelectResult?.(point.payload.result_id, revision)}
                  data={series.complete.map((row) => ({ ...row, cost: Number(row.cost_usd) }))}
                  fill="var(--chart-1)"
                />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p>{t("costSampleInsufficient")}</p>
        )}
        <details open={!series.scatter}>
          <summary className="cursor-pointer underline">{t("viewData")}</summary>
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th>{t("result")}</th>
                  <th>{dimension}</th>
                  <th>{t("currencyUsd")}</th>
                </tr>
              </thead>
              <tbody>
                {points.slice(dataOffset, dataOffset + 100).map((point) => (
                  <tr key={point.result_id}>
                    <th className="max-w-44 truncate" title={point.result_id}>
                      <button
                        type="button"
                        className="underline"
                        onClick={() => onSelectResult?.(point.result_id, revision)}
                      >
                        {point.result_id}
                      </button>
                    </th>
                    <td>{point.value ?? t("missingScore")}</td>
                    <td>{point.cost_usd ?? t("unknownCost")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {points.length > 100 && (
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={dataOffset === 0}
                onClick={() => setDataOffset((value) => Math.max(0, value - 100))}
              >
                {t("previousPage")}
              </Button>
              <Button
                variant="outline"
                disabled={dataOffset + 100 >= points.length}
                onClick={() => setDataOffset((value) => value + 100)}
              >
                {t("loadMore")}
              </Button>
            </div>
          )}
        </details>
      </div>
    </section>
  );
}

function SeriesCoverage({ metadata }: { metadata?: SeriesMetadata }) {
  const t = useTranslations("evaluations");
  return metadata ? (
    <p className="text-muted-foreground text-sm">
      {t("sampleCoverage")}: {metadata.sample_count} · {t("missingScore")}: {metadata.missing_count}{" "}
      · {t("invalidatedScore")}: {metadata.excluded_count} · {metadata.metric_version} ·{" "}
      {metadata.timezone}
    </p>
  ) : null;
}
