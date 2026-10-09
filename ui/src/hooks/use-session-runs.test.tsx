// @vitest-environment jsdom
import { act, useEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";

import type { SSEEventData } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  search: "",
  list: vi.fn(),
  replace: vi.fn(),
  scope: { scope: { userId: "u", workspaceId: "w" }, scopeRevision: 1 },
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mocks.search),
  usePathname: () => "/sessions/s",
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({ useClientDataScope: () => mocks.scope }));
vi.mock("@/lib/api/execution-view", () => ({ executionViewApi: { listRuns: mocks.list } }));
import { useSessionRuns } from "./use-session-runs";
let current: ReturnType<typeof useSessionRuns>;
function Probe({
  events = [],
  pending = false,
  revision = 0,
}: {
  events?: SSEEventData[];
  pending?: boolean;
  revision?: number;
}) {
  const value = useSessionRuns("s", events, pending, revision);
  useEffect(() => {
    current = value;
  });
  return <div>{value.state}</div>;
}
afterEach(() => {
  vi.clearAllMocks();
  mocks.search = "";
  document.body.replaceChildren();
});
test("selects authoritative session source default without overriding explicit old run", async () => {
  mocks.list.mockResolvedValue({
    items: [{ run_id: "new" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  const { root, unmount } = await renderComponent(<Probe />);
  expect(mocks.list.mock.calls[0][0]).toMatchObject({
    source_entity_type: "session",
    source_entity_id: "s",
  });
  expect(mocks.replace.mock.calls[0][0]).toContain("run=new");
  mocks.search = "run=old&at=opaque";
  await act(async () => root.render(<Probe />));
  expect(current.items[0].run_id).toBe("new");
  expect(mocks.replace).toHaveBeenCalledTimes(1);
  await unmount();
});
test("empty persisted results and incomplete projection are different states", async () => {
  mocks.list.mockResolvedValue({
    items: [],
    next_cursor: null,
    completeness: { state: "partial" },
  });
  const { unmount } = await renderComponent(<Probe />);
  expect(current.state).toBe("updating");
  expect(mocks.replace).not.toHaveBeenCalled();
  await unmount();
  mocks.list.mockResolvedValue({
    items: [],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  const second = await renderComponent(<Probe />);
  expect(current.state).toBe("empty");
  await second.unmount();
});
test("pagination is reset on source identity changes and late old replies cannot select a default", async () => {
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "first" }],
    next_cursor: "old-cursor",
    completeness: { state: "complete" },
  });
  const first = await renderComponent(<Probe />);
  let resolveOld!: (value: unknown) => void;
  mocks.list.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  await act(async () => current.loadMore());
  mocks.scope = { scope: { userId: "u", workspaceId: "other" }, scopeRevision: 2 };
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "other" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () => first.root.render(<Probe />));
  expect(mocks.list.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
  expect(current.items.map((item) => item.run_id)).toEqual(["other"]);
  await act(async () =>
    resolveOld({
      items: [{ run_id: "stale" }],
      next_cursor: null,
      completeness: { state: "complete" },
    }),
  );
  expect(current.items.map((item) => item.run_id)).toEqual(["other"]);
  await first.unmount();
  mocks.scope = { scope: { userId: "u", workspaceId: "w" }, scopeRevision: 1 };
});

test("new persisted admission refreshes the cohort and waits for its projection, not late old metadata", async () => {
  vi.useFakeTimers();
  mocks.search = "run=R1";
  const admitted = (id: string) =>
    ({
      type: "message",
      data: { role: "user", message: "turn", persist: true, run_id: id, event_id: "created-" + id },
    }) as SSEEventData;
  mocks.list.mockResolvedValue({
    items: [{ run_id: "R1" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<Probe events={[admitted("R1")]} />);
  expect(current.state).toBe("ready");
  await act(async () => result.root.render(<Probe events={[admitted("R1")]} pending />));
  expect(current.state).toBe("loading");
  await act(async () => result.root.render(<Probe events={[admitted("R1"), admitted("R2")]} />));
  expect(current.state).toBe("updating");
  mocks.list.mockResolvedValue({
    items: [{ run_id: "R2" }, { run_id: "R1" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () => vi.advanceTimersByTime(1000));
  expect(current.state).toBe("ready");
  expect(current.items[0].run_id).toBe("R2");
  expect(mocks.replace).not.toHaveBeenCalled();
  const before = mocks.list.mock.calls.length;
  await act(async () =>
    result.root.render(
      <Probe
        events={[
          admitted("R1"),
          admitted("R2"),
          {
            type: "done",
            data: { persist: true, run_id: "R1", event_id: "late-old" },
          } as SSEEventData,
        ]}
      />,
    ),
  );
  expect(current.items[0].run_id).toBe("R2");
  expect(mocks.list.mock.calls.length).toBe(before);
  await result.unmount();
  vi.useRealTimers();
});
test("confirmed empty session becomes the first persisted run without a manual refresh", async () => {
  mocks.list.mockResolvedValueOnce({
    items: [],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<Probe />);
  expect(current.state).toBe("empty");
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "first" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () =>
    result.root.render(
      <Probe
        events={[
          {
            type: "message",
            data: { role: "user", persist: true, run_id: "first", event_id: "admitted" },
          } as SSEEventData,
        ]}
      />,
    ),
  );
  expect(current.state).toBe("ready");
  expect(mocks.replace.mock.calls.at(-1)?.[0]).toContain("run=first");
  await result.unmount();
});
test("a temporarily rebuilding admission projection is retried without a manual refresh", async () => {
  vi.useFakeTimers();
  const { ApiError } = await import("@/lib/api/fetch");
  mocks.list.mockRejectedValueOnce(new ApiError(503, "projection_rebuilding")).mockResolvedValue({
    items: [{ run_id: "first" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<Probe />);
  expect(current.state).toBe("updating");
  await act(async () => vi.advanceTimersByTime(1000));
  expect(current.state).toBe("ready");
  await result.unmount();
  vi.useRealTimers();
});

test("a send ending before admission delivery must reauthorize even when its prior hint is unchanged", async () => {
  mocks.search = "run=R1";
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R1" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<Probe />);
  expect(current.state).toBe("ready");
  await act(async () => result.root.render(<Probe pending revision={1} />));
  expect(current.state).toBe("loading");
  let resolve!: (value: unknown) => void;
  mocks.list.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  await act(async () => result.root.render(<Probe revision={1} />));
  expect(current.state).toBe("loading");
  await act(async () =>
    resolve({
      items: [{ run_id: "R2" }, { run_id: "R1" }],
      next_cursor: null,
      completeness: { state: "complete" },
    }),
  );
  expect(current.items[0].run_id).toBe("R2");
  await result.unmount();
});
test("pagination cannot promote an admission-lagging cohort to current authority", async () => {
  vi.useFakeTimers();
  mocks.search = "run=R1";
  const events = [
    {
      type: "message",
      data: { role: "user", persist: true, run_id: "R2", event_id: "R2-created" },
    } as SSEEventData,
  ];
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R1" }],
    next_cursor: "old-tail",
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<Probe events={events} />);
  expect(current.state).toBe("updating");
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R0" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () => current.loadMore());
  expect(mocks.list.mock.calls.at(-1)?.[0].cursor).toBe("old-tail");
  expect(current.items.map((item) => item.run_id)).toEqual(["R1", "R0"]);
  expect(current.state).toBe("updating");
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R2" }, { run_id: "R1" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () => vi.advanceTimersByTime(1000));
  expect(mocks.list.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
  expect(current.state).toBe("ready");
  expect(current.items[0].run_id).toBe("R2");
  expect(mocks.replace).not.toHaveBeenCalled();
  await result.unmount();
  vi.useRealTimers();
});
test("normal continuation keeps first-page authority when the new page contains only older runs", async () => {
  mocks.search = "run=old";
  const events = [
    {
      type: "message",
      data: { role: "user", persist: true, run_id: "R2", event_id: "R2-created" },
    } as SSEEventData,
  ];
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R2" }],
    next_cursor: "older",
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<Probe events={events} />);
  expect(current.state).toBe("ready");
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R1" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () => current.loadMore());
  expect(current.state).toBe("ready");
  expect(current.items.map((item) => item.run_id)).toEqual(["R2", "R1"]);
  expect(mocks.replace).not.toHaveBeenCalled();
  await result.unmount();
});

test("confirmed source cohort with explicit content gaps remains browsable without clearing admission fences", async () => {
  const completeness = {
    state: "partial",
    missing_fields: ["configuration", "purpose"],
    missing_intervals: [{ start: null, end: null, reason: "pre_journal_progress_unavailable" }],
  };
  mocks.list.mockResolvedValue({
    items: [{ run_id: "R1", completeness }],
    next_cursor: null,
    completeness,
  });
  const result = await renderComponent(<Probe />);
  expect(current.state).toBe("ready");
  expect(mocks.replace.mock.calls.at(-1)?.[0]).toContain("run=R1");
  await act(async () =>
    result.root.render(
      <Probe
        events={[
          {
            type: "message",
            data: { role: "user", persist: true, run_id: "R2", event_id: "R2-created" },
          } as SSEEventData,
        ]}
      />,
    ),
  );
  expect(current.state).toBe("updating");
  expect(current.items[0].run_id).toBe("R1");
  await result.unmount();
});
