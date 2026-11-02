import { get, post, type RequestOptions } from "./fetch";

export type ToolIntentRefreshRequest = {
  command_id: string;
  status: "pending" | "applied" | "rejected";
};
const path = (run: string, activity: string) =>
  `/execution-runs/${encodeURIComponent(run)}/tool-intents/${encodeURIComponent(activity)}/refresh`;

export const toolIntentRefreshApi = {
  submit: (
    run: string,
    activity: string,
    body: { expected_subject_digest: string; reason: string },
    key: string,
    options?: RequestOptions,
  ): Promise<ToolIntentRefreshRequest> => {
    const headers = new Headers(options?.headers);
    headers.set("Idempotency-Key", key);
    return post(path(run, activity), body, {
      ...options,
      headers: Object.fromEntries(headers.entries()),
    });
  },
  status: (run: string, activity: string, command: string, options?: RequestOptions) =>
    get<ToolIntentRefreshRequest>(
      `${path(run, activity)}/${encodeURIComponent(command)}`,
      undefined,
      options,
    ),
};
