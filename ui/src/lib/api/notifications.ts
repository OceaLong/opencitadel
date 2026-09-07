import { createIngestStream, get, post } from "./fetch";
import type { NotificationsData, SSEEventHandler } from "./types";

export type NotificationDelivery = {
  id: string;
  channel_type: string;
  message: string;
  status: string;
  attempts: number;
  last_error: string | null;
  next_attempt_at: string;
  created_at: string;
  sent_at: string | null;
};

export const notificationsApi = {
  validateChannel: (channel: import("./types/patrols").PatrolNotifyChannel) =>
    post<{ valid: boolean; sent: boolean }>("/notifications/channels/validate", channel),
  testChannel: (channel: import("./types/patrols").PatrolNotifyChannel, request_id: string) =>
    post<{ delivery_id: string; status: string }>("/notifications/channels/test", {
      channel,
      request_id,
    }),
  delivery: (id: string) => get<NotificationDelivery>(`/notifications/deliveries/${id}`),
  deliveries: () => get<{ deliveries: NotificationDelivery[] }>("/notifications/deliveries"),
  retryDelivery: (id: string) =>
    post<{ retried: boolean }>(`/notifications/deliveries/${id}/retry`, {}),
  list: (unreadOnly = false): Promise<NotificationsData> => {
    return get<NotificationsData>("/notifications", { unread_only: unreadOnly });
  },

  markRead: (notificationId: string): Promise<{ read: boolean }> => {
    return post<{ read: boolean }>(`/notifications/${notificationId}/read`, {});
  },

  /**
   * 实时通知 SSE 流订阅。走仓内统一的 `createIngestStream`
   * （GET + authenticatedFetch），因此会自动携带 `X-Workspace-Id` /
   * `X-CSRF-Token` 等 header，保证与其它请求一致的租户隔离。
   */
  stream: (
    onEvent: SSEEventHandler,
    onError?: (error: Error) => void,
    eventId?: string,
    onComplete?: () => void,
  ): (() => void) => {
    return createIngestStream("/notifications/stream", onEvent, onError, eventId, onComplete);
  },
};
