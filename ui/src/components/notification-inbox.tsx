"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";

import { notificationsApi } from "@/lib/api/notifications";
import type { Notification } from "@/lib/api/types";
import { cn } from "@/lib/utils";

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function notificationHref(item: Notification): string | null {
  if (item.session_id) return `/sessions/${item.session_id}`;
  if (item.job_id) return "/automation";
  return null;
}

export function NotificationInbox({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const streamRef = useRef<EventSource | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await notificationsApi.list();
      setItems(data.notifications);
      setUnreadCount(data.unread_count);
    } catch {
      // ignore when unauthenticated
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const url = notificationsApi.streamUrl();
    const source = new EventSource(url, { withCredentials: true });
    streamRef.current = source;

    source.addEventListener("notification", () => {
      void refresh();
    });
    source.addEventListener("connected", () => {
      void refresh();
    });

    return () => {
      source.close();
      streamRef.current = null;
    };
  }, [refresh]);

  const handleMarkRead = async (item: Notification) => {
    if (item.read) return;
    try {
      await notificationsApi.markRead(item.id);
      setItems((prev) =>
        prev.map((row) => (row.id === item.id ? { ...row, read: true } : row)),
      );
      setUnreadCount((count) => Math.max(0, count - 1));
    } catch {
      // ignore
    }
  };

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="icon-sm"
          className={cn("relative", className)}
          aria-label="通知"
          title="通知"
        >
          <Bell className="size-4" />
          {unreadCount > 0 && (
            <Badge
              variant="destructive"
              className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full p-0 text-[10px]"
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </Badge>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span>通知</span>
          {loading && <span className="text-muted-foreground text-xs">刷新中…</span>}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <ScrollArea className="max-h-72">
          {items.length === 0 ? (
            <p className="text-muted-foreground px-3 py-6 text-center text-sm">暂无通知</p>
          ) : (
            items.map((item) => {
              const href = notificationHref(item);
              const content = (
                <div className="flex flex-col gap-0.5">
                  <span className={cn("text-sm", !item.read && "font-medium")}>{item.message}</span>
                  <span className="text-muted-foreground text-xs">{formatTime(item.created_at)}</span>
                </div>
              );
              return (
                <DropdownMenuItem
                  key={item.id}
                  className="cursor-pointer items-start py-2"
                  onClick={() => {
                    void handleMarkRead(item);
                    if (!href) setOpen(false);
                  }}
                  asChild={!!href}
                >
                  {href ? <Link href={href}>{content}</Link> : content}
                </DropdownMenuItem>
              );
            })
          )}
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
