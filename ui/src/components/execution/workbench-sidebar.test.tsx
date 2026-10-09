// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => ({ isMobile: false }) }));
import { useSidebar } from "@/components/ui/sidebar";

import { readLayout } from "@/lib/execution-view/layout-preferences";

import { WorkbenchSidebar } from "./workbench-sidebar";
function Toggle() {
  const sidebar = useSidebar();
  return <button onClick={sidebar.toggleSidebar}>{sidebar.state}</button>;
}
test("sidebar defaults collapsed on tablet and saves only identity-scoped layout preferences", async () => {
  const original = Object.getOwnPropertyDescriptor(window, "localStorage");
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024, writable: true });
  const scope = { userId: "sidebar-user", workspaceId: "w" };
  const result = await renderComponent(
    <WorkbenchSidebar scope={scope}>
      <Toggle />
    </WorkbenchSidebar>,
  );
  expect(result.container.textContent).toBe("collapsed");
  await act(async () => result.container.querySelector("button")!.click());
  expect(readLayout(scope).contextCollapsed).toBe(false);
  await act(async () =>
    result.root.render(
      <WorkbenchSidebar scope={{ ...scope, workspaceId: "other" }}>
        <Toggle />
      </WorkbenchSidebar>,
    ),
  );
  expect(result.container.textContent).toBe("collapsed");
  await act(async () =>
    result.root.render(
      <WorkbenchSidebar scope={scope}>
        <Toggle />
      </WorkbenchSidebar>,
    ),
  );
  expect(result.container.textContent).toBe("expanded");
  await result.unmount();
  if (original) Object.defineProperty(window, "localStorage", original);
});
