import { createIngestStream, get, post } from "./fetch";
import type { NotificationsData, SSEEventHandler } from "./types";

export const notificationsApi = {
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
