"use client";

import { useTranslations } from "next-intl";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import type { SessionMode } from "@/lib/api/types";
import { IconAgent, IconAsk } from "@/lib/icons";
import { cn } from "@/lib/utils";

type SessionModeToggleProps = {
  mode: SessionMode;
  onChange: (mode: SessionMode) => void;
  className?: string;
};

export function SessionModeToggle({ mode, onChange, className }: SessionModeToggleProps) {
  const t = useTranslations("sessionMode");

  return (
    <ToggleGroup
      type="single"
      size="sm"
      value={mode}
      onValueChange={(next) => {
        if (next) onChange(next as SessionMode);
      }}
      className={cn("bg-muted rounded-lg p-0.5", className)}
    >
      <ToggleGroupItem value="ask" className="h-7 gap-1 px-2 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
        <IconAsk className="size-3.5" />
        {t("ask")}
      </ToggleGroupItem>
      <ToggleGroupItem value="agent" className="h-7 gap-1 px-2 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
        <IconAgent className="size-3.5" />
        {t("agent")}
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
