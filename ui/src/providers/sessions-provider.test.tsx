// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  getSessions: vi.fn(),
  streamSessions: vi.fn(),
  deleteSession: vi.fn(),
  cleanup: vi.fn(),
  user: { id: "user-1" },
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/api", () => ({
  sessionApi: {
    getSessions: mocks.getSessions,
    streamSessions: mocks.streamSessions,
    deleteSession: mocks.deleteSession,
  },
}));

vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: mocks.user, loading: false }),
}));

import { SessionsProvider } from "./sessions-provider";

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("SessionsProvider stream lifecycle", () => {
  beforeEach(() => {
    mocks.getSessions.mockResolvedValue({ sessions: [] });
    mocks.streamSessions.mockReturnValue(mocks.cleanup);
  });

  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("keeps one list stream when translation identity and render state change", async () => {
    const { root, unmount } = await renderComponent(
      <SessionsProvider>
        <div>child</div>
      </SessionsProvider>,
    );
    await settle();

    expect(mocks.streamSessions).toHaveBeenCalledTimes(1);
    expect(mocks.cleanup).not.toHaveBeenCalled();

    await act(async () => {
      root.render(
        <SessionsProvider>
          <div>rerendered child</div>
        </SessionsProvider>,
      );
    });
    await settle();

    expect(mocks.streamSessions).toHaveBeenCalledTimes(1);
    expect(mocks.cleanup).not.toHaveBeenCalled();

    await unmount();
    expect(mocks.cleanup).toHaveBeenCalledTimes(1);
  });

  it("does not stream until enabled, then keeps the stream resident once activated", async () => {
    // 非活跃模块下挂载：不应发起 REST / SSE。
    const { root, unmount } = await renderComponent(
      <SessionsProvider enabled={false}>
        <div>child</div>
      </SessionsProvider>,
    );
    await settle();

    expect(mocks.getSessions).not.toHaveBeenCalled();
    expect(mocks.streamSessions).not.toHaveBeenCalled();

    // 进入 chat 模块（enabled=true）：拉起一次 REST + 一条 SSE 长连接。
    await act(async () => {
      root.render(
        <SessionsProvider enabled>
          <div>child</div>
        </SessionsProvider>,
      );
    });
    await settle();

    expect(mocks.streamSessions).toHaveBeenCalledTimes(1);
    expect(mocks.cleanup).not.toHaveBeenCalled();

    // 离开 chat 模块（enabled=false）：流常驻，不断开、不重连。
    await act(async () => {
      root.render(
        <SessionsProvider enabled={false}>
          <div>child</div>
        </SessionsProvider>,
      );
    });
    await settle();

    expect(mocks.streamSessions).toHaveBeenCalledTimes(1);
    expect(mocks.cleanup).not.toHaveBeenCalled();

    // 再次进入：仍是同一条连接，不重复建立。
    await act(async () => {
      root.render(
        <SessionsProvider enabled>
          <div>child</div>
        </SessionsProvider>,
      );
    });
    await settle();

    expect(mocks.streamSessions).toHaveBeenCalledTimes(1);
    expect(mocks.cleanup).not.toHaveBeenCalled();

    await unmount();
    expect(mocks.cleanup).toHaveBeenCalledTimes(1);
  });
});
