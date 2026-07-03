"use client";

import { SegmentedControl } from "@/components/segmented-control";
import type { SessionMode } from "@/lib/api/types";
import { IconAgent, IconAsk } from "@/lib/icons";
import { cn } from "@/lib/utils";

type SessionModeToggleProps = {
  mode: SessionMode;
  onChange: (mode: SessionMode) => void;
  className?: string;
};

export function SessionModeToggle({ mode, onChange, className }: SessionModeToggleProps) {
  return (
    <SegmentedControl
      className={cn(className)}
      value={mode}
      onChange={onChange}
      options={[
        { value: "ask", label: "Ask", icon: <IconAsk className="size-3.5" /> },
        { value: "agent", label: "Agent", icon: <IconAgent className="size-3.5" /> },
      ]}
    />
  );
}
