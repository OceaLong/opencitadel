import type { SSEEventData } from "./session";

// ==================== 自动化任务 ====================

type ScheduledJobTriggerType = "cron" | "interval" | "webhook";

type NotifyChannel = {
  type: string;
  server_id: string;
  channel_arg: string;
};

export type ScheduledJob = {
  id: string;
  name: string;
  owner_user_id: string;
  trigger_type: ScheduledJobTriggerType | string;
  trigger_spec: string;
  prompt_template: string;
  skill_id?: string | null;
  model_id?: string | null;
  knowledge_base_id?: string | null;
  operator_scope?: "owned" | "third_party_saas" | null;
  operator_domains?: string[];
  notify_channels: NotifyChannel[];
  enabled: boolean;
  timezone: string;
  source_type: "generic" | "patrol_pack";
  source_id?: string | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_run_status?: string | null;
  last_run_session_id?: string | null;
  last_run_error?: string | null;
  webhook_token?: string | null;
};

export type ScheduledJobsData = {
  jobs: ScheduledJob[];
};

export type CreateScheduledJobParams = {
  name: string;
  trigger_type?: ScheduledJobTriggerType;
  trigger_spec?: string;
  prompt_template: string;
  skill_id?: string | null;
  model_id?: string | null;
  knowledge_base_id?: string | null;
  notify_channels?: NotifyChannel[];
  operator_scope?: "owned" | "third_party_saas" | null;
  operator_domains?: string[];
  enabled?: boolean;
  timezone?: string;
};

export type UpdateScheduledJobParams = Partial<CreateScheduledJobParams>;

export type CreateScheduledJobResult = {
  job: ScheduledJob;
  webhook_secret?: string | null;
};

export type ApprovalEventData = Extract<SSEEventData, { type: "approval" }>["data"];

/**
 * 会话文件信息
 */
export type SessionFile = {
  id: string;
  filename: string;
  filepath: string;
  key: string;
  extension: string;
  content_type: string;
  size: number;
  [key: string]: unknown;
};
