"use client";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import {
  type AnalysisSelection,
  emptySelection,
  toggleDetail,
  toggleRun,
} from "@/lib/analysis-view/selection";
import type { AnalysisRun } from "@/lib/api/types/execution-analysis";

import { RunDate, RunStatus } from "./run-facts";
export function RunTable({
  rows,
  timezone,
  selection,
  onChange,
  onOpen,
  onNext,
  canNext,
  pending,
}: {
  rows: readonly AnalysisRun[];
  timezone: string;
  selection: AnalysisSelection;
  onChange: (s: AnalysisSelection) => void;
  onOpen: (id: string) => void;
  onNext: () => void;
  canNext: boolean;
  pending: boolean;
}) {
  const t = useTranslations("analysis");
  return (
    <section className="min-w-0 space-y-3">
      <h2 className="text-lg font-semibold">{t("runs")}</h2>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() =>
            onChange(rows.reduce((current, row) => toggleRun(current, row.run_id, true), selection))
          }
        >
          {t("selectPage")}
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            onChange({ ...emptySelection(), mode: "all_matching", details: selection.details })
          }
        >
          {t("selectAllMatching")}
        </Button>
        <Button variant="outline" onClick={() => onChange(emptySelection())}>
          {t("clearSelection")}
        </Button>
        <span>
          {selection.mode === "all_matching"
            ? `${t("allMatching")} − ${selection.excludedIds.length}`
            : `${t("selected")}: ${selection.runIds.length}`}{" "}
          · {t("retainedDetails")}: {selection.details.length}
          <span translate="no">/5</span>
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[48rem] text-left text-sm [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:whitespace-nowrap">
          <thead>
            <tr>
              <th>{t("selected")}</th>
              <th translate="no">Run</th>
              <th>{t("status")}</th>
              <th>{t("family")}</th>
              <th>{t("configuration")}</th>
              <th>{t("start")}</th>
              <th>{t("retainedDetails")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.run_id} id={`run-${row.run_id}`} className="border-b">
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${t("selected")} ${row.run_id}`}
                    checked={
                      selection.mode === "all_matching"
                        ? !selection.excludedIds.includes(row.run_id)
                        : selection.runIds.includes(row.run_id)
                    }
                    onChange={(e) => onChange(toggleRun(selection, row.run_id, e.target.checked))}
                  />
                </td>
                <td>
                  <button
                    {...{ elementtiming: "analysis-run" }}
                    data-native-content="analysis-run"
                    data-public-run={row.run_id}
                    className="text-primary max-w-56 p-2 text-left break-all underline"
                    onClick={() => onOpen(row.run_id)}
                  >
                    {row.run_id}
                  </button>
                </td>
                <td>
                  <RunStatus status={row.status} />
                </td>
                <td>{row.family ?? t("unknown")}</td>
                <td className="max-w-48 break-all">
                  {row.admission_configuration_id ?? t("unassignedConfiguration")}
                </td>
                <td>
                  <RunDate value={row.admitted_at} timezone={timezone} />
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${t("retainedDetails")} ${row.run_id}`}
                    checked={selection.details.includes(row.run_id)}
                    disabled={
                      !selection.details.includes(row.run_id) && selection.details.length >= 5
                    }
                    onChange={() => onChange(toggleDetail(selection, row.run_id))}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Button variant="outline" disabled={!canNext || pending} onClick={onNext}>
        {t("next")}
      </Button>
    </section>
  );
}
