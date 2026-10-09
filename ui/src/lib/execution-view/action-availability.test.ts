import { afterEach, expect, test, vi } from "vitest";

import {
  type ApprovalInboxItem,
  type ApprovalInboxResponse,
  approvalsApi,
} from "@/lib/api/approvals";
import { ApiError } from "@/lib/api/fetch";
import { sessionApi } from "@/lib/api/session";

import { canDecideApproval, decideCurrentApproval } from "./action-availability";
const target = { approvalId: "a", runId: "r", sessionId: "s" };
const item = (extra = {}): ApprovalInboxItem =>
  ({
    approval_id: "a",
    run_id: "r",
    source_entity_type: "session",
    source_entity_id: "s",
    status: "pending",
    approval_kind: "tool",
    ...extra,
  }) as ApprovalInboxItem;
afterEach(() => vi.restoreAllMocks());
test("historical pending and denied current approvals cannot be decided", () => {
  expect(canDecideApproval({ at: "old", status: "pending", allowed: true })).toBe(false);
  expect(canDecideApproval({ at: null, status: "pending", allowed: false })).toBe(false);
  expect(canDecideApproval({ at: null, status: "pending", allowed: true })).toBe(true);
});
test("paged preflight skips unrelated sources and reconciles the persisted opposing decision", async () => {
  const list = vi
    .spyOn(approvalsApi, "list")
    .mockResolvedValueOnce({
      items: Array.from({ length: 200 }, () => item({ source_entity_id: "other" })),
      limit: 200,
      offset: 0,
    })
    .mockResolvedValueOnce({ items: [item()], limit: 200, offset: 200 })
    .mockResolvedValueOnce({
      items: [item({ status: "rejected", decision: "rejected" })],
      limit: 200,
      offset: 0,
    });
  const decide = vi
    .spyOn(sessionApi, "decideApproval")
    .mockResolvedValue({ run_id: "r", approval_id: "a", decision: "approved" });
  const result = await decideCurrentApproval({
    target,
    decision: "approved",
    isCurrent: () => true,
  });
  expect(list.mock.calls[1][0]?.offset).toBe(200);
  expect(decide).toHaveBeenCalledTimes(1);
  expect(result).toMatchObject({ state: "settled", item: { decision: "rejected" } });
});
test("authority lost while preflight is pending cannot send the command", async () => {
  let resolve!: (value: ApprovalInboxResponse) => void;
  let current = true;
  vi.spyOn(approvalsApi, "list").mockReturnValue(new Promise((r) => (resolve = r)));
  const decide = vi.spyOn(sessionApi, "decideApproval");
  const pending = decideCurrentApproval({ target, decision: "approved", isCurrent: () => current });
  current = false;
  resolve({ items: [item()], limit: 200, offset: 0 });
  expect(await pending).toEqual({ state: "stale" });
  expect(decide).not.toHaveBeenCalled();
});
test.each([404, 409, 500])("HTTP %s is reconciled and never blindly retried", async (status) => {
  vi.spyOn(approvalsApi, "list")
    .mockResolvedValueOnce({ items: [item()], limit: 200, offset: 0 })
    .mockResolvedValueOnce({
      items: [item({ status: "approved", decision: "approved" })],
      limit: 200,
      offset: 0,
    });
  const decide = vi
    .spyOn(sessionApi, "decideApproval")
    .mockRejectedValue(new ApiError(status, "race"));
  expect(
    await decideCurrentApproval({ target, decision: "rejected", isCurrent: () => true }),
  ).toMatchObject({ state: "settled", item: { decision: "approved" } });
  expect(decide).toHaveBeenCalledTimes(1);
});
test("successful HTTP with still pending persistence remains unknown", async () => {
  vi.spyOn(approvalsApi, "list").mockResolvedValue({ items: [item()], limit: 200, offset: 0 });
  vi.spyOn(sessionApi, "decideApproval").mockResolvedValue({
    run_id: "r",
    approval_id: "a",
    decision: "approved",
  });
  expect(
    await decideCurrentApproval({ target, decision: "approved", isCurrent: () => true }),
  ).toMatchObject({ state: "unknown" });
});
