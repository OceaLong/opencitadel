// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { type ApprovalInboxItem, approvalsApi } from "@/lib/api/approvals";

import { renderComponent } from "@/test-utils/render";

import { ApprovalDetail } from "./approval-detail";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
const value = (approval_id = "a"): ApprovalInboxItem => ({
  approval_id,
  run_id: "r",
  source_entity_type: "session",
  source_entity_id: "s",
  status: "pending",
  approval_kind: "tool",
  subject_activity_id: "activity-a",
  subject_label: "target A",
  risk_summary: "risk A",
  decision: null,
  decided_at: null,
  decided_by_user_id: null,
  requested_at: "now",
});
afterEach(() => vi.restoreAllMocks());
test("detail approval uses its exact ID and subject, independently of the latest conversation approval", async () => {
  vi.spyOn(approvalsApi, "list").mockResolvedValue({
    items: [value("b"), value()],
    limit: 200,
    offset: 0,
  });
  const decide = vi.fn();
  const r = await renderComponent(
    <ApprovalDetail
      approval={{
        approval_id: "a",
        subject_activity_id: "activity-a",
        approval_kind: "tool",
        status: "pending",
      }}
      runId="r"
      workspaceId="w"
      context={{ sessionId: "s", allowed: true, decide, results: {}, ask: null }}
      onRevoked={() => {}}
    />,
  );
  expect(r.container.textContent).toContain("target A");
  expect(r.container.textContent).toContain("risk A");
  await act(async () =>
    Array.from(r.container.querySelectorAll("button"))
      .find((b) => b.textContent === "approve")!
      .click(),
  );
  expect(decide).toHaveBeenCalledWith("a", "approved", "", "activity-a");
  await r.unmount();
});
test("historical approval never reads current risk or exposes commands", async () => {
  const list = vi.spyOn(approvalsApi, "list");
  const r = await renderComponent(
    <ApprovalDetail
      approval={{
        approval_id: "a",
        subject_activity_id: "activity-a",
        approval_kind: "tool",
        status: "pending",
      }}
      runId="r"
      workspaceId="w"
      onRevoked={() => {}}
    />,
  );
  expect(list).not.toHaveBeenCalled();
  expect(r.container.querySelector("button")).toBeNull();
  expect(r.container.textContent).toContain("a");
  await r.unmount();
});

test("late evidence cannot overwrite a different approval identity", async () => {
  let resolve!: (value: { items: ApprovalInboxItem[]; limit: number; offset: number }) => void;
  vi.spyOn(approvalsApi, "list")
    .mockReturnValueOnce(new Promise((r) => (resolve = r)))
    .mockResolvedValue({
      items: [{ ...value("b"), subject_label: "target B", risk_summary: "risk B" }],
      limit: 200,
      offset: 0,
    });
  const props = {
    runId: "r",
    workspaceId: "w",
    context: { sessionId: "s", allowed: true, decide: vi.fn(), results: {}, ask: null },
    onRevoked: () => {},
  };
  const r = await renderComponent(
    <ApprovalDetail
      {...props}
      approval={{
        approval_id: "a",
        subject_activity_id: "activity-a",
        approval_kind: "tool",
        status: "pending",
      }}
    />,
  );
  await act(async () =>
    r.root.render(
      <ApprovalDetail
        {...props}
        approval={{
          approval_id: "b",
          subject_activity_id: "activity-a",
          approval_kind: "tool",
          status: "pending",
        }}
      />,
    ),
  );
  await act(async () => resolve({ items: [value()], limit: 200, offset: 0 }));
  expect(r.container.textContent).toContain("target B");
  expect(r.container.textContent).not.toContain("target A");
  await r.unmount();
});

import { useExecutionApprovalDecision } from "@/hooks/use-execution-approval-decision";
import { sessionApi } from "@/lib/api/session";
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: { userId: "u", workspaceId: "w" }, scopeRevision: 1 }),
}));
function CommandEvidence() {
  const commands = useExecutionApprovalDecision({
    authority: "ready",
    runId: "r",
    sessionId: "s",
    onChanged: () => {},
  });
  return (
    <ApprovalDetail
      approval={{
        approval_id: "a",
        subject_activity_id: "activity-a",
        approval_kind: "tool",
        status: "pending",
      }}
      runId="r"
      workspaceId="w"
      context={{ sessionId: "s", allowed: true, ...commands, ask: null }}
      onRevoked={() => {}}
    />
  );
}
test("evidence A followed by command-time subject B cannot send a decision", async () => {
  vi.spyOn(approvalsApi, "list")
    .mockResolvedValueOnce({ items: [value()], limit: 200, offset: 0 })
    .mockResolvedValue({
      items: [{ ...value(), subject_activity_id: "activity-b" }],
      limit: 200,
      offset: 0,
    });
  const post = vi
    .spyOn(sessionApi, "decideApproval")
    .mockResolvedValue({ approval_id: "a", run_id: "r", decision: "approved" });
  const r = await renderComponent(<CommandEvidence />);
  expect(r.container.textContent).toContain("target A");
  await act(async () =>
    Array.from(r.container.querySelectorAll("button"))
      .find((b) => b.textContent === "approve")!
      .click(),
  );
  expect(post).not.toHaveBeenCalled();
  expect(r.container.textContent).toContain("decision.unavailable");
  await r.unmount();
});
