"use client";

import { useTranslations } from "next-intl";

import type { RunView } from "@/lib/api/types/execution-view";

/** These subtotals belong to the same authorized public cut as the Run. */
export function ExecutionUsageSummary({ usage }: { usage: RunView["usage"] }) {
  const t = useTranslations("executionUsage");
  const entries = Object.entries(usage ?? {});
  if (!entries.length) return null;
  const purposes: Record<string, string> = {
    production: t("purpose.production"),
    evaluation_subject: t("purpose.evaluation_subject"),
    evaluation_judge: t("purpose.evaluation_judge"),
    unknown: t("purpose.unknown"),
  };
  const kinds: Record<string, string> = {
    main_answer: t("kind.main_answer"),
    context_summary: t("kind.context_summary"),
    other: t("kind.other"),
  };
  return (
    <section className="bg-card space-y-3 rounded-md border p-3 text-sm" aria-label={t("title")}>
      <h2 className="font-medium">{t("title")}</h2>
      <p className="text-muted-foreground text-xs">{t("knownOnly")}</p>
      {entries.map(([purpose, value]) => (
        <div key={purpose} className="space-y-2">
          <h3 className="font-medium">{purposes[purpose] ?? purposes.unknown}</h3>
          <table className="w-full text-left text-xs">
            <thead>
              <tr>
                <th>{t("group")}</th>
                <th>{t("calls")}</th>
                <th>{t("knownCost")}</th>
                <th>{t("unknownCost")}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(value.groups ?? { other: value }).map(([kind, group]) => (
                <tr key={kind} data-usage-kind={kind}>
                  <th className="font-normal">{kinds[kind] ?? kinds.other}</th>
                  <td>{group.calls}</td>
                  <td className="font-mono">{t("amountUsd", { amount: group.known_cost_usd })}</td>
                  <td>{group.unknown_cost_calls}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
