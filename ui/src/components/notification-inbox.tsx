"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { Bell } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
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
import { useAuth } from "@/providers/auth-provider";

import { translate } from "@/i18n/translate";

function formatTime(value: string, locale: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function notificationMessage(item: Notification): string {
  if (item.i18n_key) {
    return translate(item.i18n_key, item.i18n_params ?? undefined);
  }
  return item.message;
}

function notificationHref(item: Notification): string | null {
  if (item.session_id) return `/sessions/${item.session_id}`;
  if (item.job_id) return "/automation";
  return null;
}

export function NotificationInbox({ className }: { className?: string }) {
  const t = useTranslations("notifications");
  const locale = useLocale();
  const { user, loading: authLoading } = useAuth();
  const userId = user?.id ?? null;
  const authRef = useRef({ loading: authLoading, userId });
  authRef.current = { loading: authLoading, userId };
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    const requestedUserId = authRef.current.userId;
    if (authRef.current.loading || !requestedUserId) return;
    setLoading(true);
    try {
      const data = await notificationsApi.list();
      if (authRef.current.userId === requestedUserId && !authRef.current.loading) {
        setItems(data.notifications);
        setUnreadCount(data.unread_count);
      }
    } catch {
      // ignore when unauthenticated
    } finally {
      if (authRef.current.userId === requestedUserId && !authRef.current.loading) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (authLoading || !userId) {
      setOpen(false);
      setItems([]);
      setUnreadCount(0);
      setLoading(false);
      return;
    }
    void refresh();
  }, [authLoading, refresh, userId]);

  useEffect(() => {
    if (authLoading || !userId) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleReconnect = () => {
      if (cancelled || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, 3000);
    };

    const connect = () => {
      if (cancelled) return;
      cleanup = notificationsApi.stream(
        (event) => {
          // 后端在此流上发送 connected / notification / ping 事件，
          // 这些类型不在共享的 SSEEventData 联合里，故按字符串比较。
          const eventType = event.type as string;
          if (eventType === "notification" || eventType === "connected") {
            void refresh();
          }
        },
        // 连接出错：稍后重连（对齐 EventSource 的自动重连语义）
        scheduleReconnect,
        undefined,
        // 流正常结束：重连以维持长连接
        scheduleReconnect,
      );
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      cleanup?.();
    };
  }, [authLoading, refresh, userId]);

  const handleMarkRead = async (item: Notification) => {
    if (item.read) return;
    try {
      await notificationsApi.markRead(item.id);
      setItems((prev) => prev.map((row) => (row.id === item.id ? { ...row, read: true } : row)));
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
          aria-label={t("title")}
          title={t("title")}
        >
          <Bell className="size-4" />
          {unreadCount > 0 && (
            <Badge
              variant="destructive"
              className="text-2xs absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full p-0"
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </Badge>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span>{t("title")}</span>
          {loading && <span className="text-muted-foreground text-xs">{t("refreshing")}</span>}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <ScrollArea className="max-h-72">
          {items.length === 0 ? (
            <EmptyState title={t("empty")} className="py-6" />
          ) : (
            items.map((item) => {
              const href = notificationHref(item);
              const content = (
                <div className="flex flex-col gap-0.5">
                  <span className={cn("text-sm", !item.read && "font-medium")}>
                    {notificationMessage(item)}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {formatTime(item.created_at, locale)}
                  </span>
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
