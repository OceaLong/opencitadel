import type { SessionCheckpoint, SSEEventData } from "./session";

// ==================== 自动化任务 ====================

export type ScheduledJobTriggerType = "cron" | "interval" | "webhook";

export type NotifyChannel = {
  type: string;
  server_name: string;
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
  codebase_id?: string | null;
  knowledge_base_id?: string | null;
  operator_scope?: "owned" | "third_party_saas" | null;
  operator_domains?: string[];
  gate_profile?: "loose" | "standard" | "strict" | null;
  notify_channels: NotifyChannel[];
  enabled: boolean;
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
  codebase_id?: string | null;
  knowledge_base_id?: string | null;
  notify_channels?: NotifyChannel[];
  operator_scope?: "owned" | "third_party_saas" | null;
  operator_domains?: string[];
  gate_profile?: "loose" | "standard" | "strict" | null;
  enabled?: boolean;
};

export type UpdateScheduledJobParams = Partial<CreateScheduledJobParams>;

export type CreateScheduledJobResult = {
  job: ScheduledJob;
  webhook_secret?: string | null;
};

export type ApprovalEventData = Extract<SSEEventData, { type: "approval" }>["data"];

export type SessionCheckpointsData = {
  checkpoints: SessionCheckpoint[];
};

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

/**
 * 查看文件内容请求参数
 */
export type ViewFileParams = {
  filepath: string;
  [key: string]: unknown;
};

/**
 * 查看 Shell 输出请求参数
 */
export type ViewShellParams = {
  shell_session_id: string;
  [key: string]: unknown;
};
