import type { PlanEvent } from "./session";

// ==================== 通知 ====================

export type Notification = {
  id: string;
  user_id: string;
  type: string;
  session_id?: string | null;
  artifact_id?: string | null;
  job_id?: string | null;
  message: string;
  i18n_key?: string | null;
  i18n_params?: Record<string, string> | null;
  read: boolean;
  created_at: string;
};

export type NotificationsData = {
  notifications: Notification[];
  unread_count: number;
};

export type PendingPlanUpdate = {
  plan: PlanEvent;
};
