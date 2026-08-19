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
});
