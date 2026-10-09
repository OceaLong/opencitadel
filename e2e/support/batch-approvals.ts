import { pollProjection } from "./poll";

type Read = (path: string) => Promise<any>;
export type OwnedApproval = {
  approval_id: string;
  run_id: string;
  subject_activity_id: string;
  status: string;
  subject_label: string;
};

/** Every new recorded subject has its own current approval, never source authority. */
export async function approveBatchPending(
  read: Read,
  batchId: string,
  decide: (approval: OwnedApproval) => Promise<void>,
  decided: Set<string>,
) {
  const results = await read(`/evaluation/batches/${batchId}/results`);
  if (results.next_cursor)
    throw new Error("owned single-case batch unexpectedly paginated");
  const owned = new Set(
    results.items.map((row: any) => row.run_id).filter(Boolean),
  );
  for (let offset = 0; ; offset += 200) {
    const inbox = await read(
      `/approvals?status=pending&limit=200&offset=${offset}`,
    );
    for (const approval of inbox.items as OwnedApproval[]) {
      if (!owned.has(approval.run_id) || decided.has(approval.approval_id))
        continue;
      if (
        approval.status !== "pending" ||
        !approval.subject_activity_id ||
        !approval.subject_label.includes("artifact_write")
      )
        throw new Error("unexpected owned evaluation approval");
      const view = await read(`/execution-runs/${approval.run_id}/view`);
      if (
        view.run.run_id !== approval.run_id ||
        view.steps.some(
          (step: any) =>
            step.activity_id === approval.subject_activity_id &&
            step.status === "completed",
        )
      )
        throw new Error("evaluation approval identity is no longer pending");
      await decide(approval);
      await pollProjection(
        async () => {
          for (let offset = 0; ; offset += 200) {
            const current = await read(`/approvals?limit=200&offset=${offset}`);
            const item = current.items.find(
              (item: OwnedApproval) =>
                item.approval_id === approval.approval_id,
            );
            if (item || current.items.length < 200) return item;
          }
        },
        (item) =>
          item?.status === "approved" &&
          item.run_id === approval.run_id &&
          item.subject_activity_id === approval.subject_activity_id,
        { timeout: 30_000, message: "new recorded subject approval persisted" },
      );
      decided.add(approval.approval_id);
    }
    if (inbox.items.length < 200) break;
  }
}

export async function settleRecordedBatch(
  read: Read,
  batchId: string,
  decide: (approval: OwnedApproval) => Promise<void>,
) {
  const decided = new Set<string>();
  const result = await pollProjection(
    async () => {
      await approveBatchPending(read, batchId, decide, decided);
      return read(`/evaluation/batches/${batchId}`);
    },
    (batch) => batch.status === "completed" && batch.cleanup_status === "clean",
    {
      timeout: 180_000,
      message:
        "owned recorded batch gets current approval, completes and cleans",
    },
  );
  if (decided.size !== 1)
    throw new Error(
      "recorded artifact batch lacked its own one current approval",
    );
  return result;
}
