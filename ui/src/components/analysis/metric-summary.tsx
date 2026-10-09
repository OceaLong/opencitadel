"use client";
import { useTranslations } from "next-intl";

import { baselineComparison } from "@/lib/analysis-view/baseline";
import type { AnalysisSummary } from "@/lib/api/types/execution-analysis";
export function MetricSummary({
  metrics,
  baseline,
}: {
  metrics: AnalysisSummary["metrics"];
  baseline?: string | null;
}) {
  const t = useTranslations("analysis");
  const scores = metrics.scores;
  const baselineVisible = Boolean(
    baseline && scores?.series?.some((group) => group.configuration === baseline),
  );
  const comparisons = baselineVisible
    ? (scores?.comparisons ?? []).flatMap((row) => {
        const value = baselineComparison(row, baseline);
        return value ? [value] : [];
      })
    : [];
  return (
    <section className="min-w-0 space-y-3">
      <h2 className="text-lg font-semibold">{t("scoreComparisons")}</h2>
      <p>{t("heterogeneousWarning")}</p>
      <p className="break-all">
        {t("baseline")}: {baseline ?? t("unknown")}
      </p>
      {(scores?.selection_status === "unavailable" || !baselineVisible) && (
        <p>{t("selectionUnavailable")}</p>
      )}
      {comparisons.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th>{t("stratum")}</th>
                <th>{t("leftRight")}</th>
                <th>{t("pairedCases")}</th>
                <th>{t("absoluteDelta")}</th>
                <th>{t("relativeDelta")}</th>
                <th>{t("interval")}</th>
              </tr>
            </thead>
            <tbody>
              {comparisons.map(
                ({ row, configuration, delta, relative, relativeState, interval }, i) => (
                  <tr className="border-b" key={i}>
                    <td className="max-w-64 break-words">
                      {row.identity.map((v) => (Array.isArray(v) ? v.join(", ") : v)).join(" · ")}
                    </td>
                    <td className="max-w-48 break-all">
                      {configuration} / {baseline}
                    </td>
                    <td>{row.case_count}</td>
                    <td>{delta ?? t("unknown")}</td>
                    <td>
                      {relativeState === "zeroBaseline"
                        ? t("zeroBaselineNA")
                        : relative == null
                          ? t("unknown")
                          : `${(relative * 100).toFixed(2)}%`}
                    </td>
                    <td>
                      {interval?.join(" – ") ?? t("unknown")} · {row.bootstrap_samples}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <p>{t("noPairedComparison")}</p>
      )}
      <p className="text-muted-foreground text-sm">{t("pairedDirection")}</p>
      <details>
        <summary>{t("viewData")}</summary>
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th>{t("stratum")}</th>
                <th>{t("configuration")}</th>
                <th>{t("metric")}</th>
                <th>{t("value")}</th>
                <th>{t("coverage")}</th>
              </tr>
            </thead>
            <tbody>
              {scores?.series?.flatMap((group) =>
                Object.entries(group.metrics).map(([name, m]) => (
                  <tr
                    key={JSON.stringify([
                      group.identity,
                      group.applicable_dimensions,
                      group.configuration,
                      name,
                    ])}
                  >
                    <td className="max-w-64 break-all">{group.identity.join(" · ")}</td>
                    <td className="break-all">{group.configuration}</td>
                    <td>{name}</td>
                    <td>
                      {m.value == null
                        ? t("unknown")
                        : m.unit === "ratio"
                          ? `${(m.value * 100).toFixed(2)}%`
                          : m.value}{" "}
                      {m.unit !== "ratio" ? m.unit : ""}
                    </td>
                    <td>
                      {t("sampleSymbol")}={m.sample_count}; {m.numerator ?? "—"}/
                      {m.denominator ?? "—"}; {t("missing")}={m.missing_count}; {t("excluded")}=
                      {m.excluded_count}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
