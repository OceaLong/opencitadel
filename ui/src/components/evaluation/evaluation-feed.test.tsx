// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError } from "@/lib/api/fetch";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({ events: vi.fn() }));
vi.mock("@/lib/api/evaluations", () => ({ evaluationApi: { batchEvents: mocks.events } }));
import { useEvaluationFeed } from "./evaluation-feed";
const access = { workspaceId: "w", canManage: false, canRun: false, canRegister: false };
function Feed({ refresh, deny }: { refresh: () => void; deny: () => void }) {
  useEvaluationFeed({ ...access, deny }, "b", refresh);
  return null;
}
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});
test("stream disconnect causes one authoritative refresh and reconnect, preserving caller scope", async () => {
  vi.useFakeTimers();
  const refresh = vi.fn(),
    deny = vi.fn();
  mocks.events.mockResolvedValueOnce(undefined).mockImplementation(() => new Promise(() => {}));
  const view = await renderComponent(<Feed refresh={refresh} deny={deny} />);
  expect(refresh).toHaveBeenCalledOnce();
  await act(async () => {
    vi.advanceTimersByTime(3000);
  });
  expect(mocks.events).toHaveBeenCalledTimes(2);
  expect(mocks.events.mock.calls[1][1]).toMatchObject({ workspaceId: "w" });
  const signal = mocks.events.mock.calls[1][1].signal;
  await view.unmount();
  expect(signal.aborted).toBe(true);
});
test("revocation clears the owning boundary and never reconnects", async () => {
  vi.useFakeTimers();
  const deny = vi.fn(),
    refresh = vi.fn();
  mocks.events.mockRejectedValue(new ApiError(403, "denied"));
  const view = await renderComponent(<Feed refresh={refresh} deny={deny} />);
  expect(deny).toHaveBeenCalledOnce();
  await act(async () => {
    vi.advanceTimersByTime(30000);
  });
  expect(mocks.events).toHaveBeenCalledOnce();
  expect(refresh).not.toHaveBeenCalled();
  await view.unmount();
});
