// @vitest-environment jsdom

import { act } from "react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  auth: { loading: false, user: { id: "user-1" } as { id: string } | null },
  list: vi.fn(),
}));

vi.mock("next-intl", () => mockNextIntl());

vi.mock("next/navigation", () => ({
  usePathname: () => "/sessions",
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={typeof href === "string" ? href : undefined} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api/approvals", () => ({
  approvalsApi: { list: mocks.list },
}));

vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => mocks.auth,
}));

import { APPROVALS_CHANGED_EVENT } from "@/lib/events";

import { ApprovalsIndicator } from "./approvals-indicator";

describe("ApprovalsIndicator event-driven refresh", () => {
  beforeEach(() => {
    mocks.auth.loading = false;
    mocks.auth.user = { id: "user-1" };
    mocks.list.mockResolvedValue({ items: [{ approval_id: "a-1" }] });
  });

  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("re-polls immediately on APPROVALS_CHANGED_EVENT and stops after unmount", async () => {
    const { container, unmount } = await renderComponent(<ApprovalsIndicator />);

    // 挂载即拉取一次，角标显示待审批数量
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("1");

    // 审批被决定 → 事件驱动立即重拉，角标回落
    mocks.list.mockResolvedValue({ items: [] });
    await act(async () => {
      window.dispatchEvent(new CustomEvent(APPROVALS_CHANGED_EVENT));
      await Promise.resolve();
    });
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("1");

    // 卸载后监听被清理，事件不再触发请求
    await unmount();
    window.dispatchEvent(new CustomEvent(APPROVALS_CHANGED_EVENT));
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });
});
