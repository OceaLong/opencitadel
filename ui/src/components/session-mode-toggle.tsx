"use client";

import { Bot, MessageCircleQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SessionMode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type SessionModeToggleProps = {
  mode: SessionMode;
  onChange: (mode: SessionMode) => void;
  className?: string;
};

export function SessionModeToggle({ mode, onChange, className }: SessionModeToggleProps) {
  return (
    <div className={cn("bg-muted flex rounded-lg p-0.5", className)}>
      <Button
        type="button"
        variant={mode === "ask" ? "default" : "ghost"}
        size="sm"
        className="h-7 gap-1 px-2 text-xs"
        onClick={() => onChange("ask")}
      >
        <MessageCircleQuestion className="size-3.5" />
        Ask
      </Button>
      <Button
        type="button"
        variant={mode === "agent" ? "default" : "ghost"}
        size="sm"
        className="h-7 gap-1 px-2 text-xs"
        onClick={() => onChange("agent")}
      >
        <Bot className="size-3.5" />
        Agent
      </Button>
    </div>
  );
}
