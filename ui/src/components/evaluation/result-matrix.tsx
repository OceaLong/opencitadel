"use client";
import { type CSSProperties, useId, useState, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { numericScore } from "@/lib/evaluation-view/matrix";

const narrowQuery = "(max-width: 639px)";
function watchNarrow(change: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const media = window.matchMedia(narrowQuery);
  media.addEventListener("change", change);
  return () => media.removeEventListener("change", change);
}
const isNarrow = () =>
  typeof window.matchMedia === "function" && window.matchMedia(narrowQuery).matches;
/** Presentation identity retains the selected physical attempt and score cut separately. */
export type MatrixResult = {
  id: string;
  configId: string;
  repetition: number;
  attempt: number;
  runId: string | null;
  evaluationRevision: number;
  resultRevision: number;
  executionStatus: string;
  scoringStatus: string;
  value: number | null;
};
export type MatrixRow = { id: string; label: string; results: readonly MatrixResult[] };
export type MatrixConfig = { id: string; label: string };
export function ResultMatrix({
  rows,
  renderIdentity,
  configs,
  selection,
  onSelectResult,
  onLoadMore,
}: {
  renderIdentity?: {
    scopeId: string;
    batchId: string;
    snapshotId: string;
    revision: number;
    ready: boolean;
  };
  rows: readonly MatrixRow[];
  configs: readonly MatrixConfig[];
  selection: string | null;
  onSelectResult: (result: MatrixResult) => void;
  onLoadMore?: () => void;
}) {
  const t = useTranslations("evaluations");
  const [scrollTop, setScrollTop] = useState(0);
  const [mobileConfig, setMobileConfig] = useState<string | null>(null);
  const [chosenResults, setChosenResults] = useState<Record<string, string>>({});
  const [fullLabel, setFullLabel] = useState<string | null>(null);
  const activeConfig = configs.some((config) => config.id === mobileConfig)
    ? mobileConfig
    : configs[0]?.id;
  const narrow = useSyncExternalStore(watchNarrow, isNarrow, () => false);
  const visibleConfigs = narrow ? configs.filter((config) => config.id === activeConfig) : configs;
  const tableId = useId();
  const rowHeight = 88;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 3);
  const end = Math.min(rows.length, start + 16);
  return (
    <section
      data-native-view="matrix"
      data-native-ready={renderIdentity?.ready ?? false}
      data-public-scope={renderIdentity?.scopeId}
      data-public-batch={renderIdentity?.batchId}
      data-public-snapshot={renderIdentity?.snapshotId}
      data-public-revision={renderIdentity?.revision}
      aria-label={t("resultMatrix")}
      className="min-w-0 space-y-3"
    >
      {fullLabel && (
        <div className="rounded border p-3">
          <p className="break-words">{fullLabel}</p>
          <Button variant="ghost" onClick={() => setFullLabel(null)}>
            {t("close")}
          </Button>
        </div>
      )}
      <div
        role="tablist"
        aria-label={t("configs")}
        className="flex gap-2 overflow-x-auto sm:hidden"
      >
        {configs.map((config, index) => (
          <button
            type="button"
            role="tab"
            aria-selected={activeConfig === config.id}
            aria-controls={tableId}
            tabIndex={activeConfig === config.id ? 0 : -1}
            className="shrink-0 rounded border px-3 py-2 text-sm"
            key={config.id}
            onClick={() => setMobileConfig(config.id)}
            onKeyDown={(event) => {
              const next =
                event.key === "ArrowRight"
                  ? (index + 1) % configs.length
                  : event.key === "ArrowLeft"
                    ? (index + configs.length - 1) % configs.length
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? configs.length - 1
                        : null;
              if (next !== null) {
                event.preventDefault();
                setMobileConfig(configs[next].id);
                event.currentTarget.parentElement
                  ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
                  [next]?.focus();
              }
            }}
          >
            {config.label}
          </button>
        ))}
      </div>
      {!rows.length && <p role="status">{t("matrixEmpty")}</p>}
      {!!rows.length && (
        <div
          data-native-scroll="matrix"
          id={tableId}
          tabIndex={0}
          aria-label={t("resultMatrix")}
          className="max-h-[560px] overflow-auto rounded-md border"
          onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        >
          <table
            className="w-full table-fixed border-collapse text-sm sm:min-w-[var(--matrix-width)]"
            style={{ "--matrix-width": `${216 + configs.length * 152}px` } as CSSProperties}
            aria-rowcount={rows.length + 1}
          >
            <thead className="bg-background sticky top-0 z-20">
              <tr>
                <th
                  scope="col"
                  className="bg-background sticky left-0 w-[152px] border-b p-3 text-left sm:w-[216px]"
                >
                  {t("caseKey")}
                </th>
                {visibleConfigs.map((config) => (
                  <th
                    scope="col"
                    className={`${config.id === activeConfig ? "" : "hidden sm:table-cell"} min-w-36 border-b p-3 text-left break-words sm:min-w-[152px]`}
                    key={config.id}
                  >
                    {config.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {start > 0 && (
                <tr aria-hidden="true">
                  <td colSpan={visibleConfigs.length + 1} style={{ height: start * rowHeight }} />
                </tr>
              )}
              {rows.slice(start, end).map((row, index) => (
                <tr key={row.id} aria-rowindex={start + index + 2} style={{ height: rowHeight }}>
                  <th
                    scope="row"
                    title={row.label}
                    className="bg-background sticky left-0 z-10 border-b p-3 text-left"
                  >
                    <button
                      type="button"
                      className="line-clamp-2 w-full text-left break-words"
                      onClick={() => setFullLabel(row.label)}
                    >
                      {row.label}
                    </button>
                  </th>
                  {visibleConfigs.map((config) => (
                    <td
                      key={config.id}
                      className={`${config.id === activeConfig ? "" : "hidden sm:table-cell"} border-b p-2 align-top`}
                    >
                      <div className="space-y-1">
                        {row.results.filter((result) => result.configId === config.id).length >
                          1 && (
                          <select
                            aria-label={`${row.label} · ${config.label} · ${t("repetition")}`}
                            className="bg-background w-full rounded border text-xs"
                            value={
                              chosenResults[`${row.id}:${config.id}`] ??
                              row.results.find((result) => result.configId === config.id)?.id
                            }
                            onChange={(event) =>
                              setChosenResults((previous) => ({
                                ...previous,
                                [`${row.id}:${config.id}`]: event.target.value,
                              }))
                            }
                          >
                            {row.results
                              .filter((result) => result.configId === config.id)
                              .map((result) => (
                                <option key={result.id} value={result.id}>
                                  {t("repetition")} {result.repetition + 1} · {t("attempt")}{" "}
                                  {result.attempt + 1}
                                </option>
                              ))}
                          </select>
                        )}
                        {row.results
                          .filter(
                            (result) =>
                              result.configId === config.id &&
                              result.id ===
                                (chosenResults[`${row.id}:${config.id}`] ??
                                  row.results.find((item) => item.configId === config.id)?.id),
                          )
                          .map((result) => {
                            const failed = [
                              "failed",
                              "mismatch",
                              "blocked",
                              "blocked_budget",
                              "unknown",
                              "cancelled",
                            ].includes(result.executionStatus);
                            const value = numericScore(result.value);
                            const state =
                              result.executionStatus === "blocked_budget"
                                ? "stateBlockedBudget"
                                : failed
                                  ? "matrixFailed"
                                  : result.executionStatus !== "succeeded"
                                    ? "matrixNotRun"
                                    : value === null
                                      ? ["pending", "running"].includes(result.scoringStatus)
                                        ? "matrixPendingScore"
                                        : "missingScore"
                                      : "matrixScored";
                            return (
                              <button
                                type="button"
                                key={JSON.stringify([
                                  renderIdentity?.snapshotId,
                                  result.id,
                                  result.evaluationRevision,
                                  result.resultRevision,
                                  result.value,
                                  result.executionStatus,
                                  result.scoringStatus,
                                ])}
                                data-native-content="matrix-result"
                                {...{ elementtiming: "evaluation-result" }}
                                data-public-result={result.id}
                                data-public-case={row.id}
                                data-public-config={result.configId}
                                data-public-repetition={result.repetition}
                                data-public-attempt={result.attempt}
                                data-public-run={result.runId}
                                data-public-evaluation-revision={result.evaluationRevision}
                                data-public-result-revision={result.resultRevision}
                                aria-pressed={selection === result.id}
                                className="hover:bg-muted focus-visible:ring-ring block w-full rounded border px-2 py-1 text-left break-words whitespace-normal focus-visible:ring-2"
                                onClick={() => onSelectResult(result)}
                              >
                                <span aria-hidden="true">
                                  {failed
                                    ? "×"
                                    : state === "matrixPendingScore"
                                      ? "◷"
                                      : state === "matrixNotRun"
                                        ? "□"
                                        : value === null
                                          ? "○"
                                          : "●"}{" "}
                                </span>
                                {t(state)}
                                {value !== null &&
                                  ` · ${state === "matrixScored" ? "" : t("matrixScored") + " "}${value}`}
                                <span className="text-muted-foreground block text-xs">
                                  {t("repetition")} {result.repetition + 1} · {t("attempt")}{" "}
                                  {result.attempt + 1}
                                </span>
                              </button>
                            );
                          })}
                        {!row.results.some((result) => result.configId === config.id) && (
                          <span>○ {t("matrixNotRun")}</span>
                        )}
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
              {end < rows.length && (
                <tr aria-hidden="true">
                  <td
                    colSpan={visibleConfigs.length + 1}
                    style={{ height: (rows.length - end) * rowHeight }}
                  />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {onLoadMore && (
        <Button variant="outline" onClick={onLoadMore}>
          {t("loadMore")}
        </Button>
      )}
    </section>
  );
}
