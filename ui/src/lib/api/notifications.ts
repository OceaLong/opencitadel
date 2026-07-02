import { API_CONFIG } from "./fetch";
import { get, post } from "./fetch";
import type { NotificationsData } from "./types";

export const notificationsApi = {
  list: (unreadOnly = false): Promise<NotificationsData> => {
    return get<NotificationsData>("/notifications", { unread_only: unreadOnly });
  },

  markRead: (notificationId: string): Promise<{ read: boolean }> => {
    return post<{ read: boolean }>(`/notifications/${notificationId}/read`, {});
  },

  /** EventSource URL for live notification stream (uses cookie auth). */
  streamUrl: (): string => {
    const base = API_CONFIG.baseURL.replace(/\/$/, "");
    return `${base}/notifications/stream`;
  },
};
