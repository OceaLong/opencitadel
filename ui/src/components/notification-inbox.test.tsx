// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  auth: { loading: true, user: null as { id: string } | null },
  list: vi.fn(),
  markRead: vi.fn(),
  stream: vi.fn(),
  close: vi.fn(),
}));

vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/api/notifications", () => ({
  notificationsApi: {
    list: mocks.list,
    markRead: mocks.markRead,
    stream: mocks.stream,
  },
}));

vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => mocks.auth,
}));

import { NotificationInbox } from "./notification-inbox";

async function rerender(root: Awaited<ReturnType<typeof renderComponent>>["root"]) {
  await act(async () => {
    root.render(<NotificationInbox />);
    await Promise.resolve();
  });
}

describe("NotificationInbox authentication lifecycle", () => {
  beforeEach(() => {
    mocks.auth.loading = true;
    mocks.auth.user = null;
    mocks.list.mockResolvedValue({ notifications: [], unread_count: 0 });
    // stream() returns a cleanup fn; it never fires onError/onComplete here,
    // so no reconnect is scheduled and it opens exactly once per user.
    mocks.stream.mockReturnValue(mocks.close);
  });

  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("opens authenticated resources only for a user and drains them on logout", async () => {
    const { root, unmount } = await renderComponent(<NotificationInbox />);

    expect(mocks.list).not.toHaveBeenCalled();
    expect(mocks.stream).not.toHaveBeenCalled();

    mocks.auth.loading = false;
    await rerender(root);
    expect(mocks.list).not.toHaveBeenCalled();
    expect(mocks.stream).not.toHaveBeenCalled();

    mocks.auth.user = { id: "user-1" };
    await rerender(root);
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(mocks.stream).toHaveBeenCalledTimes(1);

    mocks.auth.user = null;
    await rerender(root);
    expect(mocks.close).toHaveBeenCalledTimes(1);
    expect(mocks.list).toHaveBeenCalledTimes(1);

    await unmount();
  });
});
