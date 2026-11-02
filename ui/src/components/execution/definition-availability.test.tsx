// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { DefinitionAvailability } from "@/lib/api/types/execution-view";

import { renderComponent } from "@/test-utils/render";

import {
  DefinitionAvailabilityNotice,
  ExecutionDefinitionAvailability,
} from "./definition-availability";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "owner" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: { userId: "owner", workspaceId: "team" }, scopeRevision: 1 }),
}));
const observation = (
  state: DefinitionAvailability["state"],
  run_id = "r",
): DefinitionAvailability => ({
  run_id,
  state,
  reason: state === "version_unavailable" ? "VERSION_UNAVAILABLE" : null,
  definition_version: 1,
  definition_digest: "a".repeat(64),
  checked_at: "2026-10-16T01:00:00Z",
});
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

test("TTL polling observes unavailable and recovery without a Run event", async () => {
  const read = vi
    .spyOn(executionViewApi, "getDefinitionAvailability")
    .mockResolvedValueOnce(observation("available"))
    .mockResolvedValueOnce(observation("version_unavailable"))
    .mockResolvedValueOnce(observation("available"))
    .mockResolvedValue(observation("terminal"));
  const view = await renderComponent(<DefinitionAvailabilityNotice runId="r" workspaceId="team" />);
  expect(view.container.textContent).toBe("");
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  expect(view.container.textContent).toContain("versionUnavailable");
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  expect(view.container.textContent).toBe("");
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  await act(async () => vi.advanceTimersByTimeAsync(60000));
  expect(read).toHaveBeenCalledTimes(4);
  expect(read.mock.calls[0][1]?.workspaceId).toBe("team");
  await view.unmount();
});

test("authorization failure clears old diagnostics and stops polling", async () => {
  const read = vi
    .spyOn(executionViewApi, "getDefinitionAvailability")
    .mockResolvedValueOnce(observation("version_unavailable"))
    .mockRejectedValue(new ApiError(403, "permission_denied"));
  const view = await renderComponent(<DefinitionAvailabilityNotice runId="r" workspaceId="team" />);
  expect(view.container.textContent).toContain("versionUnavailable");
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  expect(view.container.textContent).toBe("");
  await act(async () => vi.advanceTimersByTimeAsync(60000));
  expect(read).toHaveBeenCalledTimes(2);
  await view.unmount();
});

test("navigation aborts stale responses and never displays another Run", async () => {
  let complete!: (value: DefinitionAvailability) => void;
  const read = vi
    .spyOn(executionViewApi, "getDefinitionAvailability")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    )
    .mockResolvedValue(observation("available", "new"));
  const view = await renderComponent(<DefinitionAvailabilityNotice runId="r" workspaceId="team" />);
  const oldSignal = read.mock.calls[0][1]?.signal;
  await act(async () =>
    view.root.render(<DefinitionAvailabilityNotice runId="new" workspaceId="other" />),
  );
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => complete(observation("version_unavailable")));
  expect(view.container.textContent).toBe("");
  await view.unmount();
});

test("historical playback never reads current worker availability", async () => {
  const read = vi.spyOn(executionViewApi, "getDefinitionAvailability");
  const view = await renderComponent(<ExecutionDefinitionAvailability runId="r" historical />);
  expect(read).not.toHaveBeenCalled();
  expect(view.container.textContent).toBe("");
  await view.unmount();
});
