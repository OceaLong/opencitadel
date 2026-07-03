"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type SegmentedControlOption<T extends string> = {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
};

type SegmentedControlProps<T extends string> = {
  value: T;
  options: SegmentedControlOption<T>[];
  onChange: (value: T) => void;
  className?: string;
  size?: "sm" | "default";
};

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  className,
  size = "sm",
}: SegmentedControlProps<T>) {
  return (
    <div className={cn("bg-muted flex rounded-lg p-0.5", className)} role="group">
      {options.map((option) => (
        <Button
          key={option.value}
          type="button"
          variant={value === option.value ? "default" : "ghost"}
          size={size}
          className={cn("h-7 gap-1 px-2 text-xs", size === "default" && "h-8 px-3")}
          onClick={() => onChange(option.value)}
          aria-pressed={value === option.value}
        >
          {option.icon}
          {option.label}
        </Button>
      ))}
    </div>
  );
}
