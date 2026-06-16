"use client";

import { cn } from "@/lib/utils";

import type { ConnectionStatus } from "@/hooks/use-room-sse";

const LABELS: Record<ConnectionStatus, string> = {
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  disconnected: "已断开",
};

const COLORS: Record<ConnectionStatus, string> = {
  connecting: "bg-amber-500",
  connected: "bg-emerald-500",
  reconnecting: "bg-amber-500 animate-pulse",
  disconnected: "bg-rose-500",
};

type ConnectionStatusBadgeProps = {
  status: ConnectionStatus;
  className?: string;
};

export function ConnectionStatusBadge({ status, className }: ConnectionStatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-background/80 px-2 py-0.5 text-[10px] text-muted-foreground",
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", COLORS[status])} />
      {LABELS[status]}
    </span>
  );
}
