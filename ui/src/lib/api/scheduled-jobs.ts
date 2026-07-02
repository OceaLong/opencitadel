import { del, get, post } from "./fetch";
import type {
  CreateScheduledJobParams,
  CreateScheduledJobResult,
  ScheduledJobsData,
} from "./types";

export const scheduledJobsApi = {
  list: (): Promise<ScheduledJobsData> => {
    return get<ScheduledJobsData>("/scheduled-jobs");
  },

  create: (params: CreateScheduledJobParams): Promise<CreateScheduledJobResult> => {
    return post<CreateScheduledJobResult>("/scheduled-jobs", params);
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
};
