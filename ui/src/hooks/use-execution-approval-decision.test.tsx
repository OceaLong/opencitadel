// @vitest-environment jsdom
import { act, useEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";

import {
  type ApprovalInboxItem,
  type ApprovalInboxResponse,
  approvalsApi,
} from "@/lib/api/approvals";
import { sessionApi } from "@/lib/api/session";

import { renderComponent } from "@/test-utils/render";

import { useExecutionApprovalDecision } from "./use-execution-approval-decision";
const scope = vi.hoisted(() => ({ revision: 1 }));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    scope: { userId: "u", workspaceId: "w" },
    scopeRevision: scope.revision,
  }),
}));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
let command: ReturnType<typeof useExecutionApprovalDecision>;
const item = {
  approval_id: "a",
  run_id: "r",
  source_entity_type: "session",
  source_entity_id: "s",
  status: "pending",
} as ApprovalInboxItem;
function Harness({ authority = "ready", run = "r" }: { authority?: string | null; run?: string }) {
  const state = useExecutionApprovalDecision({
    authority,
    runId: run,
    sessionId: "s",
    onChanged: () => {},
  });
  useEffect(() => {
    command = state;
  });
  return <p>{state.results.a?.state}</p>;
}
afterEach(() => {
  vi.restoreAllMocks();
  scope.revision = 1;
});
test("same approval cannot be sent twice and an unknown result has no blind retry", async () => {
  vi.spyOn(approvalsApi, "list").mockResolvedValue({ items: [item], limit: 200, offset: 0 });
  const post = vi
    .spyOn(sessionApi, "decideApproval")
    .mockResolvedValue({ approval_id: "a", run_id: "r", decision: "approved" });
  const r = await renderComponent(<Harness />);
  await act(async () => {
    await Promise.all([command.decide("a", "approved"), command.decide("a", "approved")]);
  });
  expect(r.container.textContent).toBe("unknown");
  expect(post).toHaveBeenCalledTimes(1);
  await act(async () => command.decide("a", "rejected"));
  expect(post).toHaveBeenCalledTimes(1);
  await r.unmount();
});
test.each(["scope", "run", "authority"])(
  "%s change cancels captured preflight and stale callbacks",
  async (change) => {
    let resolve!: (v: ApprovalInboxResponse) => void;
    vi.spyOn(approvalsApi, "list").mockReturnValue(new Promise((r) => (resolve = r)));
    const post = vi.spyOn(sessionApi, "decideApproval");
    const r = await renderComponent(<Harness />);
    const stale = command.decide;
    let pending!: Promise<void>;
    await act(async () => {
      pending = command.decide("a", "approved");
    });
    if (change === "scope") scope.revision++;
    await act(async () =>
      r.root.render(
        <Harness
          authority={change === "authority" ? null : "ready"}
          run={change === "run" ? "new" : "r"}
        />,
      ),
    );
    await act(async () => {
      resolve({ items: [item], limit: 200, offset: 0 });
      await pending;
      await stale("a", "approved");
    });
    expect(post).not.toHaveBeenCalled();
    await r.unmount();
  },
);

test("a cancelled preflight that never sent a write can be explicitly decided after fresh authority", async () => {
  let resolve!: (value: ApprovalInboxResponse) => void;
  const list = vi
    .spyOn(approvalsApi, "list")
    .mockReturnValueOnce(new Promise((r) => (resolve = r)))
    .mockResolvedValue({ items: [item], limit: 200, offset: 0 });
  const post = vi
    .spyOn(sessionApi, "decideApproval")
    .mockResolvedValue({ approval_id: "a", run_id: "r", decision: "approved" });
  const r = await renderComponent(<Harness />);
  let pending!: Promise<void>;
  await act(async () => {
    pending = command.decide("a", "approved");
  });
  expect(list).toHaveBeenCalledTimes(1);
  await act(async () => r.root.render(<Harness authority={null} />));
  await act(async () => {
    resolve({ items: [item], limit: 200, offset: 0 });
    await pending;
  });
  expect(post).not.toHaveBeenCalled();
  await act(async () => r.root.render(<Harness />));
  await act(async () => command.decide("a", "approved"));
  expect(post).toHaveBeenCalledTimes(1);
  await r.unmount();
});

test("refresh replaces unknown with exact persisted decision without unlocking or sending another write", async () => {
  const list = vi
    .spyOn(approvalsApi, "list")
    .mockResolvedValue({ items: [item], limit: 200, offset: 0 });
  const post = vi
    .spyOn(sessionApi, "decideApproval")
    .mockResolvedValue({ approval_id: "a", run_id: "r", decision: "approved" });
  const r = await renderComponent(<Harness />);
  await act(async () => command.decide("a", "approved"));
  expect(r.container.textContent).toBe("unknown");
  list.mockResolvedValue({
    items: [{ ...item, status: "rejected", decision: "rejected" }],
    limit: 200,
    offset: 0,
  });
  await act(async () => r.root.render(<Harness authority="fresh" />));
  expect(command.results.a).toMatchObject({ state: "settled", item: { decision: "rejected" } });
  await act(async () => command.decide("a", "approved"));
  expect(post).toHaveBeenCalledTimes(1);
  await r.unmount();
});
test("a late refresh reconciliation cannot publish into a different Run identity", async () => {
  const list = vi
    .spyOn(approvalsApi, "list")
    .mockResolvedValue({ items: [item], limit: 200, offset: 0 });
  vi.spyOn(sessionApi, "decideApproval").mockResolvedValue({
    approval_id: "a",
    run_id: "r",
    decision: "approved",
  });
  const r = await renderComponent(<Harness />);
  await act(async () => command.decide("a", "approved"));
  let resolve!: (response: ApprovalInboxResponse) => void;
  list.mockReturnValue(new Promise((r) => (resolve = r)));
  await act(async () => r.root.render(<Harness authority="fresh" />));
  expect(list).toHaveBeenCalledTimes(3);
  await act(async () => r.root.render(<Harness run="new" />));
  await act(async () =>
    resolve({
      items: [{ ...item, status: "approved", decision: "approved" }],
      limit: 200,
      offset: 0,
    }),
  );
  expect(command.results.a).toBeUndefined();
  expect(r.container.textContent).toBe("");
  await r.unmount();
});

test("a persisted refresh result wins over a late old command reply in the same Run", async () => {
  const list = vi
    .spyOn(approvalsApi, "list")
    .mockResolvedValue({ items: [item], limit: 200, offset: 0 });
  let resolve!: (value: { run_id: string; approval_id: string; decision: string }) => void;
  const post = vi
    .spyOn(sessionApi, "decideApproval")
    .mockReturnValue(new Promise((r) => (resolve = r)));
  const r = await renderComponent(<Harness />);
  let pending!: Promise<void>;
  await act(async () => {
    pending = command.decide("a", "approved");
  });
  expect(post).toHaveBeenCalledTimes(1);
  list.mockResolvedValue({
    items: [{ ...item, status: "rejected", decision: "rejected" }],
    limit: 200,
    offset: 0,
  });
  await act(async () => r.root.render(<Harness authority="fresh" />));
  expect(command.results.a).toMatchObject({ state: "settled", item: { decision: "rejected" } });
  await act(async () => {
    resolve({ run_id: "r", approval_id: "a", decision: "approved" });
    await pending;
  });
  expect(command.results.a).toMatchObject({ state: "settled", item: { decision: "rejected" } });
  expect(post).toHaveBeenCalledTimes(1);
  await r.unmount();
});
