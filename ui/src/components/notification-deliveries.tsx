"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocale } from "next-intl";

import { Button } from "@/components/ui/button";

import { type NotificationDelivery, notificationsApi } from "@/lib/api/notifications";

export function NotificationDeliveries() {
  const zh = useLocale().startsWith("zh");
  const [items, setItems] = useState<NotificationDelivery[]>([]);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setItems((await notificationsApi.deliveries()).deliveries);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const labels: Record<string, string> = zh
    ? {
        pending: "待发送",
        sending: "发送中",
        retrying: "等待重试",
        sent: "已发送",
        failed: "发送失败",
      }
    : {
        pending: "Pending",
        sending: "Sending",
        retrying: "Retry scheduled",
        sent: "Sent",
        failed: "Failed",
      };
  return (
    <section className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{zh ? "通知投递记录" : "Notification deliveries"}</h2>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          {zh ? "刷新" : "Refresh"}
        </Button>
      </div>
      {error && (
        <p role="alert">{zh ? "加载或重试失败，请重试" : "Could not load or retry deliveries"}</p>
      )}
      {!error && items.length === 0 && (
        <p className="text-muted-foreground text-sm">{zh ? "暂无投递记录" : "No deliveries yet"}</p>
      )}
      {items.map((item) => (
        <div key={item.id} className="space-y-1 border-t pt-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span>
              {item.channel_type} · {labels[item.status] ?? item.status} · {item.attempts}{" "}
              {zh ? "次尝试" : "attempts"}
            </span>
            {["failed", "retrying"].includes(item.status) && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={async () => {
                  setBusy(item.id);
                  try {
                    await notificationsApi.retryDelivery(item.id);
                    await refresh();
                  } catch {
                    setError(true);
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                {zh ? "重新投递" : "Retry delivery"}
              </Button>
            )}
          </div>
          <p>{item.message}</p>
          {item.last_error && <p className="text-destructive">{item.last_error}</p>}
          {item.status === "retrying" && (
            <p>
              {zh ? "下次尝试：" : "Next attempt: "}
              {new Date(item.next_attempt_at).toLocaleString()}
            </p>
          )}
        </div>
      ))}
    </section>
  );
}
