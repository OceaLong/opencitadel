import { del, get, patch, post } from "./fetch";
import type {
  CreateScheduledJobParams,
  CreateScheduledJobResult,
  ScheduledJob,
  ScheduledJobsData,
  UpdateScheduledJobParams,
} from "./types";

export const scheduledJobsApi = {
  list: (): Promise<ScheduledJobsData> => {
    return get<ScheduledJobsData>("/scheduled-jobs");
  },

  create: (params: CreateScheduledJobParams): Promise<CreateScheduledJobResult> => {
    return post<CreateScheduledJobResult>("/scheduled-jobs", params);
  },

  update: (jobId: string, params: UpdateScheduledJobParams): Promise<ScheduledJob> => {
    return patch<ScheduledJob>(`/scheduled-jobs/${jobId}`, params);
  },

  delete: (jobId: string): Promise<{ deleted: boolean }> => {
    return del<{ deleted: boolean }>(`/scheduled-jobs/${jobId}`);
  },

  rotateSecret: (
    jobId: string,
  ): Promise<{ webhook_secret: string; webhook_token: string }> => {
    return post<{ webhook_secret: string; webhook_token: string }>(
      `/scheduled-jobs/${jobId}/rotate-secret`,
      {},
    );
  },

  trigger: (jobId: string): Promise<{ session_id: string }> => {
    return post<{ session_id: string }>(`/scheduled-jobs/${jobId}/trigger`, {});
  },
};
