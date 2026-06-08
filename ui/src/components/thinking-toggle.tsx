"use client";

import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";

import { cn } from "@/lib/utils";

export type ThinkingToggleProps = {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  className?: string;
};

export function ThinkingToggle({
  enabled,
  onChange,
  disabled = false,
  className,
}: ThinkingToggleProps) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-pressed={enabled}
      aria-label={enabled ? "关闭思考模式" : "开启思考模式"}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className={cn(
        "h-8 max-w-[120px] shrink-0 gap-1 px-2 text-xs font-normal",
        enabled
          ? "text-foreground bg-accent/60 hover:bg-accent hover:text-foreground"
          : "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      <span className="relative inline-flex items-center justify-center">
        <Sparkles className={cn("size-4", enabled ? "text-primary" : "text-muted-foreground")} />
        {enabled && (
          <span className="bg-primary text-primary-foreground absolute -right-0.5 -bottom-0.5 flex size-3 items-center justify-center rounded-full text-[8px]">
            ✓
          </span>
        )}
      </span>
      <span>思考</span>
    </Button>
  );
}
