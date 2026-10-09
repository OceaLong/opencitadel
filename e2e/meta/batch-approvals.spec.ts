import { expect, test } from "@playwright/test";
import { approveBatchPending } from "../support/batch-approvals";

for (const batch of ["shared", "keyboard-independent"]) {
  test(`${batch} approves only its new pending subject, never source or another batch`, async () => {
    const current = {
      approval_id: `${batch}-approval`,
      run_id: `${batch}-run`,
      subject_activity_id: `${batch}-activity`,
      status: "pending",
      subject_label: "artifact_write",
    };
    const decided: string[] = [];
    const read = async (path: string) =>
      path.startsWith("/approvals?limit=")
        ? { items: [{ ...current, status: "approved" }] }
        : path.endsWith("/results")
          ? { items: [{ run_id: current.run_id }], next_cursor: null }
          : path.startsWith("/approvals?")
            ? {
                items: [
                  { ...current, approval_id: "source", run_id: "source-run" },
                  current,
                  { ...current, approval_id: "foreign", run_id: "other-run" },
                ],
              }
            : { run: { run_id: current.run_id }, steps: [] };
    const seen = new Set<string>();
    await approveBatchPending(
      read,
      batch,
      async (item) => {
        decided.push(item.approval_id);
      },
      seen,
    );
    await approveBatchPending(
      read,
      batch,
      async (item) => {
        decided.push(item.approval_id);
      },
      seen,
    );
    expect(decided).toEqual([current.approval_id]);
    await expect(
      approveBatchPending(
        async (path) =>
          path.endsWith("/view")
            ? { run: { run_id: "wrong" }, steps: [] }
            : read(path),
        batch,
        async () => {
          throw new Error("must not decide");
        },
        new Set(),
      ),
    ).rejects.toThrow("identity");
  });
}
