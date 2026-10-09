"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

import type { RunView } from "@/lib/api/types/execution-view";

type Props = {
  at: string | null;
  latestAvailable: string | null;
  start?: string | null;
  currentTime?: string | null;
  coverage?: RunView["completeness"];
  onSeekTime: (time: string, release?: boolean) => void;
  onSeekEvent: (direction: "before" | "after") => void;
  onReturnLive: () => void;
};
export function PlaybackControls({
  at,
  latestAvailable,
  start,
  currentTime,
  coverage,
  onSeekTime,
  onSeekEvent,
  onReturnLive,
}: Props) {
  const t = useTranslations("executionWorkbench");
  const [draft, setDraft] = useState<{ at: string | null; value: number } | null>(null);
  const min = Date.parse(start ?? ""),
    max = Date.parse(latestAvailable ?? "");
  const value = draft?.at === at ? draft.value : Date.parse(currentTime ?? latestAvailable ?? "");
  const disabled =
    !Number.isFinite(min) ||
    !Number.isFinite(max) ||
    max < min ||
    !coverage ||
    coverage.state === "unavailable" ||
    coverage.state === "rebuilding";
  const scrubber = (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <label className="text-muted-foreground text-xs">
        {t("seekTime")}
        <input
          aria-label={t("seekTime")}
          type="range"
          className="accent-primary w-full"
          min={disabled ? 0 : min}
          max={disabled ? 1 : max}
          step={1}
          value={disabled ? 0 : Math.max(min, Math.min(max, value))}
          disabled={disabled}
          onChange={(event) => {
            const next = Number(event.target.value);
            setDraft({ at, value: next });
            // Every intent must reach the coordinator to cancel an older timer/request,
            // including a target in a known gap.
            onSeekTime(new Date(next).toISOString());
          }}
          onPointerUp={(event) => {
            if (!disabled)
              onSeekTime(new Date(Number(event.currentTarget.value)).toISOString(), true);
          }}
          onKeyUp={(event) => {
            if (
              !disabled &&
              ["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)
            )
              onSeekTime(new Date(Number(event.currentTarget.value)).toISOString(), true);
          }}
        />
      </label>
      <span className="truncate font-mono text-xs">{currentTime ?? t("live")}</span>
      {coverage?.state !== "complete" && <span className="text-xs">{t("playbackCoverage")}</span>}
    </div>
  );
  return (
    <div
      data-testid="playback-controls"
      role="group"
      className="flex min-h-14 min-w-0 items-center gap-2 px-3 py-2"
      aria-label={t("playback")}
      onKeyDown={(event) => {
        if (
          event.altKey ||
          event.ctrlKey ||
          event.metaKey ||
          (event.target as HTMLElement).closest(
            "input, textarea, select, [contenteditable=true], [role=dialog]",
          )
        )
          return;
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        if (event.key === "ArrowLeft") onSeekEvent("before");
        else if (at) onSeekEvent("after");
      }}
    >
      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {currentTime ?? t("live")}
      </span>
      <Button
        aria-label={t("previousEvent")}
        className="h-9 min-w-9"
        variant="ghost"
        size="sm"
        onClick={() => onSeekEvent("before")}
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        <span className="hidden md:inline">{t("previousEvent")}</span>
      </Button>
      <Button
        aria-label={t("nextEvent")}
        className="h-9 min-w-9"
        variant="ghost"
        size="sm"
        onClick={() => onSeekEvent("after")}
        disabled={!at}
      >
        <ChevronRight className="size-4" aria-hidden="true" />
        <span className="hidden md:inline">{t("nextEvent")}</span>
      </Button>
      <div className="hidden min-w-0 flex-1 md:flex">{scrubber}</div>
      <Sheet>
        <SheetTrigger asChild>
          <Button variant="outline" size="sm" className="md:hidden">
            {t("seekTime")}
          </Button>
        </SheetTrigger>
        <SheetContent side="bottom">
          <SheetHeader>
            <SheetTitle>{t("playback")}</SheetTitle>
          </SheetHeader>
          <div className="p-4">{scrubber}</div>
        </SheetContent>
      </Sheet>
      <div className="hidden min-w-0 text-xs lg:block">
        <span>{t("latestAvailable")}</span>
        <time className="block truncate font-mono">{latestAvailable ?? t("unknown")}</time>
      </div>
      {at && (
        <Button variant="outline" size="sm" onClick={onReturnLive}>
          {t("returnLive")}
        </Button>
      )}
    </div>
  );
}
