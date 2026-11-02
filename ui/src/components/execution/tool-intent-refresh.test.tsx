// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ApiError } from "@/lib/api/fetch";
import { toolIntentRefreshApi } from "@/lib/api/tool-intent-refresh";

import { renderComponent } from "@/test-utils/render";

import { RefreshForm, ToolIntentRefreshAction } from "./tool-intent-refresh";

const auth = vi.hoisted(() => ({ user: { id: "owner", global_role: "user" }, loading: false }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/providers/auth-provider", () => ({ useAuth: () => auth }));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    scope: { userId: "owner", workspaceId: "personal" },
    scopeRevision: 1,
  }),
}));
const props = () => ({
  runId: "run",
  activityId: "activity",
  subjectDigest: "a".repeat(64),
  onChanged: vi.fn(),
  onRevoked: vi.fn(),
});
async function reason(container: HTMLElement, value = "Reviewed updated policy") {
  await act(async () => {
    const input = container.querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(
      input,
      value,
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
beforeEach(() => {
  auth.user.global_role = "user";
  vi.spyOn(toolIntentRefreshApi, "submit").mockResolvedValue({
    command_id: "refresh-1",
    status: "pending",
  });
  vi.spyOn(toolIntentRefreshApi, "status").mockResolvedValue({
    command_id: "refresh-1",
    status: "pending",
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.replaceChildren();
});

test("requires reason and binds current subject without changing original arguments", async () => {
  const values = props();
  const r = await renderComponent(<RefreshForm {...values} workspaceId="personal" />);
  expect(r.container.querySelector("button")!.disabled).toBe(true);
  await reason(r.container);
  await act(async () => r.container.querySelector("button")!.click());
  expect(toolIntentRefreshApi.submit).toHaveBeenCalledWith(
    "run",
    "activity",
    { expected_subject_digest: "a".repeat(64), reason: "Reviewed updated policy" },
    expect.any(String),
    expect.objectContaining({ workspaceId: "personal" }),
  );
  expect(r.container.textContent).toContain("refreshQueued");
  expect(r.container.querySelector("button")!.disabled).toBe(true);
  expect(values.onChanged).not.toHaveBeenCalled();
  await r.unmount();
});

test("ambiguous response retry reuses identity; accepted status refreshes live detail", async () => {
  vi.mocked(toolIntentRefreshApi.submit).mockRejectedValueOnce(new Error("response lost"));
  vi.mocked(toolIntentRefreshApi.status).mockResolvedValue({
    command_id: "refresh-1",
    status: "applied",
  });
  const values = props();
  const r = await renderComponent(<RefreshForm {...values} workspaceId="personal" />);
  await reason(r.container);
  await act(async () => r.container.querySelector("button")!.click());
  await act(async () => r.container.querySelector("button")!.click());
  const calls = vi.mocked(toolIntentRefreshApi.submit).mock.calls;
  expect(calls[0][3]).toBe(calls[1][3]);
  expect(values.onChanged).toHaveBeenCalledOnce();
  expect(r.container.textContent).toContain("refreshApplied");
  await r.unmount();
});

test("revoked status access stops polling and requests private detail removal", async () => {
  vi.useFakeTimers();
  vi.mocked(toolIntentRefreshApi.status).mockRejectedValue(new ApiError(403, "denied"));
  const values = props();
  const r = await renderComponent(<RefreshForm {...values} workspaceId="personal" />);
  await reason(r.container);
  await act(async () => r.container.querySelector("button")!.click());
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  expect(values.onRevoked).toHaveBeenCalledOnce();
  expect(toolIntentRefreshApi.status).toHaveBeenCalledOnce();
  await r.unmount();
});

test("auditor cannot see the renewal command", async () => {
  auth.user.global_role = "auditor";
  const r = await renderComponent(<ToolIntentRefreshAction {...props()} />);
  expect(r.container.querySelector("button")).toBeNull();
  expect(toolIntentRefreshApi.submit).not.toHaveBeenCalled();
  await r.unmount();
});
