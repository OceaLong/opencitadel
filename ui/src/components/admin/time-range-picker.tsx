"use client";

import type { AdminTimeRange } from "@/lib/admin-utils";
import { cn } from "@/lib/utils";

const OPTIONS: Array<{ value: AdminTimeRange; label: string }> = [
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "90d", label: "近 90 天" },
  { value: "all", label: "全部" },
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
          {option.label}
        </button>
      ))}
    </div>
  );
}
