"use client";

import { cn } from "@/lib/utils";

type OpenCitadelIconProps = {
  variant?: "full" | "icon";
  className?: string;
};

/** Citadel keep with crenellations and negative-space arched gate (fill-rule: evenodd). */
const CITADEL_PATH =
  "M4 12 H7 V9 H11 V12 H14 V9 H18 V12 H21 V9 H25 V12 H28 V28 H4 Z M13 28 V21 C13 19.3 14.3 18 16 18 C17.7 18 19 19.3 19 21 V28 Z";

function CitadelMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("shrink-0", className)}
      aria-hidden
    >
      <path d={CITADEL_PATH} fill="currentColor" fillRule="evenodd" />
    </svg>
  );
}

export function OpenCitadelIcon({ variant = "full", className }: OpenCitadelIconProps) {
  if (variant === "icon") {
    return (
      <span
        className={cn(
          "text-foreground inline-flex size-7 items-center justify-center",
          className,
        )}
        aria-hidden
      >
        <CitadelMark className="size-7" />
      </span>
    );
  }

  return (
    <span className={cn("text-foreground inline-flex items-center gap-1.5", className)}>
      <CitadelMark className="size-5" />
      <span className="text-sm font-semibold tracking-tight">OpenCitadel</span>
    </span>
  );
}
