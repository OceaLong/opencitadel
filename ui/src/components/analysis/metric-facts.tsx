"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import type { AnalysisSummary } from "@/lib/api/types/execution-analysis";

/** Inspect server facts without inventing a pooled percentile or overlapping duration sum. */
export function MetricFacts({ metrics }: { metrics: AnalysisSummary["metrics"] }) {
  const t = useTranslations("analysis");
  const [kind, setKind] = useState<"runs" | "activity">("runs");
  const [page, setPage] = useState(0);
  const groups =
    kind === "runs"
      ? (metrics.series ?? [])
      : [...(metrics.intervals ?? []), ...(metrics.approvals ?? [])];
  const visible = groups.slice(page * 25, page * 25 + 25);
  return (
    <details className="space-y-3">
      <summary className="cursor-pointer font-medium">{t("operationalFacts")}</summary>
      <p className="text-sm">{t("usageWarning")}</p>
      <h3 className="font-medium">{t("usageFacts")}</h3>
      <p>
        {t("accounting")}: {metrics.usage?.grain ?? t("unknown")} · {t("accountingRuns")}:{" "}
        {metrics.usage?.accounting_run_count ?? t("unknown")}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>{t("purpose")}</th>
              <th>{t("metric")}</th>
              <th>{t("value")}</th>
              <th>{t("coverage")}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(metrics.usage?.purposes ?? {}).flatMap(([purpose, values]) =>
              Object.entries(values).map(([name, metric]) => (
                <tr key={`${purpose}:${name}`} className="border-b">
                  <td>{purpose}</td>
                  <td>{name}</td>
                  <td>
                    {metric.value == null
                      ? t("unknown")
                      : metric.unit === "ratio"
                        ? `${(Number(metric.value) * 100).toFixed(2)}%`
                        : `${metric.value} ${metric.unit}`}
                  </td>
                  <td>
                    {t("sampleSymbol")}={metric.sample_count}; {t("missing")}={metric.missing_count}
                    ; {metric.numerator ?? "—"}/{metric.denominator ?? "—"}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => {
            setKind("runs");
            setPage(0);
          }}
        >
          {t("runFacts")}
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            setKind("activity");
            setPage(0);
          }}
        >
          {t("activityFacts")}
        </Button>
      </div>
      <div className="max-h-96 overflow-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>{t("stratum")}</th>
              <th>{t("bucket")}</th>
              <th>{t("metric")}</th>
              <th>{t("value")}</th>
              <th>{t("coverage")}</th>
            </tr>
          </thead>
          <tbody>
            {visible.flatMap((series, groupIndex) =>
              Object.entries(series.metrics).map(([name, metric]) => (
                <tr key={`${groupIndex}:${name}`} className="border-b">
                  <td className="max-w-64 break-all">
                    {[
                      series.group.configuration_revision ?? t("unassignedConfiguration"),
                      series.group.family,
                      series.group.purpose,
                      series.group.execution_mode,
                    ].join(" · ")}
                  </td>
                  <td>{series.group.bucket}</td>
                  <td>{name}</td>
                  <td>
                    {metric.value == null
                      ? t("unknown")
                      : metric.unit === "ratio"
                        ? `${(metric.value * 100).toFixed(2)}%`
                        : `${metric.value} ${metric.unit}`}
                  </td>
                  <td>
                    {t("sampleSymbol")}={metric.sample_count}; {metric.numerator ?? "—"}/
                    {metric.denominator ?? "—"}; {t("missing")}={metric.missing_count};{" "}
                    {t("excluded")}={metric.excluded_count}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2">
        <Button variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
          {t("previous")}
        </Button>
        <Button
          variant="outline"
          disabled={(page + 1) * 25 >= groups.length}
          onClick={() => setPage(page + 1)}
        >
          {t("next")}
        </Button>
      </div>
    </details>
  );
}
