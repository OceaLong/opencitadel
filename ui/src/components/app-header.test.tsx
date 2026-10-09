// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const title = "Long observed label 长标签 ".repeat(8);
vi.mock("next/navigation", () => ({ usePathname: () => "/sessions/owned" }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/hooks/use-nav-modules", () => ({
  useNavModules: () => ({
    activeModule: { key: "chat", href: "/sessions" },
    modules: [{ key: "chat" }],
  }),
}));
vi.mock("@/hooks/use-capabilities", () => ({
  useCapabilities: () => ({ capability: () => ({ state: "available" }) }),
}));
vi.mock("@/providers/page-title-provider", () => ({ usePageTitle: () => title }));
vi.mock("@/providers/settings-dialog-provider", () => ({
  useSettingsDialog: () => ({ openSettings: vi.fn() }),
}));
vi.mock("@/components/approvals-indicator", () => ({ ApprovalsIndicator: () => null }));
vi.mock("@/components/notification-inbox", () => ({ NotificationInbox: () => null }));
import { AppHeader } from "./app-header";

test("current title opens complete metadata and closes with focus return", async () => {
  const result = await renderComponent(<AppHeader />);
  const trigger = result.container.querySelector<HTMLButtonElement>(
    'button[aria-label^="showFullTitle"]',
  );
  expect(trigger).not.toBeNull();
  expect(trigger!.closest('[aria-current="page"]')).not.toBeNull();
  await act(async () => {
    trigger!.focus();
    trigger!.click();
  });
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain(title);
  await act(async () => {
    document
      .querySelector('[role="dialog"]')!
      .dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
  await vi.waitFor(() => expect(document.activeElement).toBe(trigger));
  await result.unmount();
});
