import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { appApi } from "./api";
import { createSession, readChatStream } from "./execution";
import { acceptanceId } from "./ids";
import { pollProjection } from "./poll";

/** Drive only approvals belonging to the run created in this owned session. */
export async function completedOwnedRun(
  page: Page,
  modelId: string,
  message: string,
  mode: "ask" | "agent" = "agent",
) {
  const session = await createSession(
    page,
    acceptanceId("owned-source"),
    mode,
    modelId,
  );
  await readChatStream(
    page,
    session,
    {
      message,
      mode,
      model_id: modelId,
      request_id: randomUUID(),
    },
    { stopAfter: 1 },
  );
  const approvals: string[] = [];
  const decided = new Set<string>();
  const view = await pollProjection(
    async () => {
      const runs = (
        await appApi<any>(
          page,
          `/execution-runs?source_entity_type=session&source_entity_id=${session}`,
        )
      ).data.items;
      if (!runs.length) return null;
      expect(runs).toHaveLength(1);
      const runId = runs[0].run_id;
      const inbox = (
        await appApi<any>(page, "/approvals?status=pending&limit=200")
      ).data;
      for (const item of inbox.items.filter(
        (row: any) => row.run_id === runId && !decided.has(row.approval_id),
      )) {
        await appApi(
          page,
          `/approval-batches/${item.approval_id}/commands/decide`,
          {
            method: "POST",
            body: {
              decision: "approved",
              feedback: "Exact owned acceptance run",
            },
          },
        );
        decided.add(item.approval_id);
        approvals.push(item.approval_id);
      }
      return (await appApi<any>(page, `/execution-runs/${runId}/view`)).data;
    },
    (value) => value?.run.status === "completed",
    {
      timeout: 180_000,
      message: "exact owned run completes with actual approval decisions",
    },
  );
  expect(view.run.status).toBe("completed");
  return { session, view, approvals };
}
