"use client";

import { useTranslations } from "next-intl";

import type { AdminTimeRange } from "@/lib/admin-utils";
import { cn } from "@/lib/utils";

const OPTIONS: Array<{ value: AdminTimeRange; labelKey: "timeRange7d" | "timeRange30d" | "timeRange90d" | "timeRangeAll" }> = [
  { value: "7d", labelKey: "timeRange7d" },
  { value: "30d", labelKey: "timeRange30d" },
  { value: "90d", labelKey: "timeRange90d" },
  { value: "all", labelKey: "timeRangeAll" },
];

export function AdminTimeRangePicker({
  value,
  onChange,
  className,
}: {
  value: AdminTimeRange;
  onChange: (value: AdminTimeRange) => void;
  className?: string;
}) {
  const t = useTranslations("admin");

  return (
    <div className={cn("bg-muted/40 inline-flex rounded-xl border p-1", className)}>
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
            value === option.value
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(option.labelKey)}
        </button>
      ))}
    </div>
  );
}
