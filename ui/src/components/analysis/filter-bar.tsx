"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { fromUtcInput, recentRange, utcInput } from "@/lib/analysis-view/time-range";
import type { SummaryQuery } from "@/lib/api/types/execution-analysis";
export function FilterBar({
  value,
  onChange,
  onApply,
  pending,
}: {
  value: SummaryQuery;
  onChange: (value: SummaryQuery) => void;
  onApply: () => void;
  pending: boolean;
}) {
  const t = useTranslations("analysis");
  const [configurationText, setConfigurationText] = useState(
    value.filters?.comparison_config_version_ids?.join(", ") ?? "",
  );
  const fields = [
    "session",
    "model_revision",
    "family",
    "tool",
    "status",
    "mode",
    "purpose",
    "batch_id",
  ] as const;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onApply();
      }}
      className="space-y-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm">{t("quickRange")}</span>
        {[1, 7, 30, 90].map((days) => (
          <Button
            key={days}
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({ ...value, filters: { ...value.filters, ...recentRange(days) } })
            }
          >
            {days === 1 ? t("last24h") : t("lastDays", { days })}
          </Button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="min-w-0 text-sm">
          {t("startUtc")}
          <input
            type="datetime-local"
            required
            className="bg-background mt-1 block w-full min-w-0 rounded border p-2"
            value={utcInput(value.filters?.start)}
            onChange={(e) =>
              onChange({
                ...value,
                filters: { ...value.filters, start: fromUtcInput(e.target.value) },
              })
            }
          />
        </label>
        <label className="min-w-0 text-sm">
          {t("endUtc")}
          <input
            type="datetime-local"
            required
            className="bg-background mt-1 block w-full min-w-0 rounded border p-2"
            value={utcInput(value.filters?.end)}
            onChange={(e) =>
              onChange({
                ...value,
                filters: { ...value.filters, end: fromUtcInput(e.target.value) },
              })
            }
          />
        </label>
        <label className="min-w-0 text-sm">
          {t("configuration_revision")}
          <input
            className="bg-background mt-1 block w-full min-w-0 rounded border p-2"
            value={value.filters?.configuration_revision ?? ""}
            onChange={(e) =>
              onChange({
                ...value,
                filters: { ...value.filters, configuration_revision: e.target.value || undefined },
              })
            }
          />
        </label>
        <label className="text-sm">
          {t("grain")}
          <select
            className="bg-background mt-1 block w-full rounded border p-2"
            value={value.grain}
            onChange={(e) => onChange({ ...value, grain: e.target.value as "hour" | "day" })}
          >
            <option value="day">{t("day")}</option>
            <option value="hour">{t("hour")}</option>
          </select>
        </label>
        <label className="text-sm">
          {t("timezone")}
          <input
            className="bg-background mt-1 block w-full rounded border p-2"
            value={value.timezone}
            onChange={(e) => onChange({ ...value, timezone: e.target.value })}
          />
        </label>
      </div>
      <details>
        <summary className="cursor-pointer text-sm">{t("advancedFilters")}</summary>
        <div className="grid gap-3 py-3 sm:grid-cols-2 lg:grid-cols-4">
          {fields.map((key) => (
            <label key={key} className="min-w-0 text-sm">
              {t(key)}
              <input
                className="bg-background mt-1 block w-full min-w-0 rounded border p-2"
                value={value.filters?.[key] ?? ""}
                onChange={(e) =>
                  onChange({
                    ...value,
                    filters: { ...value.filters, [key]: e.target.value || undefined },
                  })
                }
              />
            </label>
          ))}
          <label className="text-sm">
            {t("comparisonConfigurations")}
            <input
              className="bg-background mt-1 block w-full rounded border p-2"
              value={configurationText}
              onChange={(e) => {
                setConfigurationText(e.target.value);
                const ids = e.target.value
                  .split(",")
                  .map((id) => id.trim())
                  .filter(Boolean);
                e.target.setCustomValidity(ids.length > 5 ? t("fiveConfigurations") : "");
                onChange({
                  ...value,
                  filters: { ...value.filters, comparison_config_version_ids: ids },
                });
              }}
            />
          </label>
          <label className="text-sm">
            {t("accounting")}
            <select
              className="bg-background mt-1 block w-full rounded border p-2"
              value={value.filters?.accounting ?? "run"}
              onChange={(e) =>
                onChange({
                  ...value,
                  filters: {
                    ...value.filters,
                    accounting: e.target.value as "run" | "selected_result" | "batch_total",
                  },
                })
              }
            >
              <option value="run">{t("runAccounting")}</option>
              <option value="selected_result">{t("resultAccounting")}</option>
              <option value="batch_total">{t("batchAccounting")}</option>
            </select>
          </label>
        </div>
      </details>
      <Button disabled={pending}>{t("applyFilters")}</Button>
    </form>
  );
}
