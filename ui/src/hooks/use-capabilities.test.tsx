// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  auth: { loading: true, user: null as { id: string } | null },
  get: vi.fn(),
  workspaceId: "",
  scopeRevision: 0,
}));

vi.mock("@/lib/api/capabilities", () => ({
  capabilitiesApi: { get: mocks.get },
}));

vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => mocks.auth,
}));

vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    scope: mocks.auth.user ? { userId: mocks.auth.user.id, workspaceId: mocks.workspaceId } : null,
    scopeRevision: mocks.scopeRevision,
  }),
}));

import { CAPABILITIES_CHANGED_EVENT } from "@/lib/events";

import { useCapabilities } from "./use-capabilities";

function Probe() {
  const { capability, loading } = useCapabilities();
  return <div>{loading ? "loading" : (capability("chat")?.state ?? "empty")}</div>;
}

async function rerender(root: Awaited<ReturnType<typeof renderComponent>>["root"]) {
  await act(async () => {
    root.render(<Probe />);
    await Promise.resolve();
  });
}

describe("useCapabilities authentication lifecycle", () => {
  beforeEach(() => {
    mocks.workspaceId = "";
    mocks.scopeRevision = 0;
    mocks.auth.loading = true;
    mocks.auth.user = null;
    mocks.get.mockResolvedValue({
      items: { chat: { state: "available" } },
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("loads only for an authenticated user and clears the snapshot on logout", async () => {
    const { container, root, unmount } = await renderComponent(<Probe />);

    expect(mocks.get).not.toHaveBeenCalled();
    expect(container.textContent).toBe("loading");

    mocks.auth.loading = false;
    await rerender(root);
    expect(mocks.get).not.toHaveBeenCalled();
    expect(container.textContent).toBe("empty");

    mocks.auth.user = { id: "user-1" };
    await rerender(root);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("available");

    mocks.auth.user = null;
    await rerender(root);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("empty");

    await unmount();
  });

  it("reloads on CAPABILITIES_CHANGED_EVENT and window focus, and cleans up on unmount", async () => {
    mocks.auth.loading = false;
    mocks.auth.user = { id: "user-1" };
    const { container, unmount } = await renderComponent(<Probe />);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("available");

    // 推理配置保存成功 → 事件驱动立即重拉
    mocks.get.mockResolvedValue({ items: { chat: { state: "unavailable" } } });
    await act(async () => {
      window.dispatchEvent(new CustomEvent(CAPABILITIES_CHANGED_EVENT));
      await Promise.resolve();
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(container.textContent).toBe("unavailable");

    // 窗口重获焦点 → 重拉
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
    });
    expect(mocks.get).toHaveBeenCalledTimes(3);

    // 卸载后监听全部清理，事件不再触发请求
    await unmount();
    window.dispatchEvent(new CustomEvent(CAPABILITIES_CHANGED_EVENT));
    window.dispatchEvent(new Event("focus"));
    expect(mocks.get).toHaveBeenCalledTimes(3);
  });
});

it("rejects late old-workspace success, error and finally", async () => {
  mocks.auth.loading = false;
  mocks.auth.user = { id: "user-1" };
  let oldResolve!: (value: unknown) => void;
  let nextResolve!: (value: unknown) => void;
  mocks.get.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        oldResolve = resolve;
      }),
  );
  const { container, root, unmount } = await renderComponent(<Probe />);
  mocks.workspaceId = "team-2";
  mocks.scopeRevision++;
  mocks.get.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        nextResolve = resolve;
      }),
  );
  await rerender(root);
  await act(async () => {
    oldResolve({ items: { chat: { state: "available" } } });
  });
  expect(container.textContent).toBe("loading");
  expect(mocks.get.mock.calls.at(-1)?.[0]).toMatchObject({ workspaceId: "team-2" });
  await act(async () => {
    nextResolve({ items: { chat: { state: "denied" } } });
  });
  expect(container.textContent).toBe("denied");
  await unmount();
});
