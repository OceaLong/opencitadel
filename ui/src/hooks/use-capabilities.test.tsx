// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  auth: { loading: true, user: null as { id: string } | null },
  get: vi.fn(),
}));

vi.mock("@/lib/api/capabilities", () => ({
  capabilitiesApi: { get: mocks.get },
}));

vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => mocks.auth,
}));

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
});
