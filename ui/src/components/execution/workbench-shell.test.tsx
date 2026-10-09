// @vitest-environment jsdom
import { act, useState } from "react";
import { expect, test, vi } from "vitest";

import type { WorkbenchLayout } from "@/lib/execution-view/layout-preferences";
import { parseSelection } from "@/lib/execution-view/url-state";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: { count?: number }) => key + (values?.count ?? ""),
}));
import { WorkbenchShell } from "./workbench-shell";
function Probe({ count = 1 }: { count?: number }) {
  const [selection, setSelection] = useState(parseSelection("run=r").selection);
  const [layout, setLayout] = useState<WorkbenchLayout>({});
  return (
    <WorkbenchShell
      selection={selection}
      onSelectionChange={setSelection}
      layout={layout}
      onLayoutChange={setLayout}
      view={<div>{selection.view}</div>}
      chat={<input defaultValue="draft" />}
      detail={<div>detail body</div>}
      messageCount={count}
    />
  );
}
test("conversation collapse retains draft and counts new messages", async () => {
  const { container, root, unmount } = await renderComponent(<Probe />);
  const button = () =>
    [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("conversation"))!;
  await act(async () => button().click());
  expect(container.querySelector("input")?.value).toBe("draft");
  expect(container.querySelector("[data-conversation]")?.hasAttribute("hidden")).toBe(true);
  await act(async () => root.render(<Probe count={3} />));
  expect(button().textContent).toContain("newMessages2");
  await act(async () => button().click());
  expect(container.querySelector("input")?.value).toBe("draft");
  await unmount();
});
test("keyboard tabs retain run identity", async () => {
  const { container, unmount } = await renderComponent(<Probe />);
  const tabs = container.querySelectorAll<HTMLButtonElement>('[role="tab"]');
  await act(async () => {
    tabs[0].focus();
    tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  });
  await vi.waitFor(() => expect(tabs[1].getAttribute("aria-selected")).toBe("true"));
  expect(container.textContent).toContain("r");
  await unmount();
});
test("desktop detail and conversation resize by keyboard; tablet and mobile use a sheet", async () => {
  const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 1104,
    right: 1104,
    height: 800,
    top: 0,
    left: 0,
    bottom: 800,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  const original = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440, writable: true });
  function Geometry() {
    const [layout, setLayout] = useState<WorkbenchLayout>({});
    return (
      <WorkbenchShell
        selection={parseSelection("run=r&step=s").selection}
        onSelectionChange={() => {}}
        layout={layout}
        onLayoutChange={setLayout}
        view={<div>main</div>}
        detail={<div>detail body</div>}
        chat={<input />}
      />
    );
  }
  const result = await renderComponent(<Geometry />);
  const detail = result.container.querySelector('[aria-label="resizeDetail"]')!;
  expect(detail).not.toBeNull();
  await act(async () =>
    detail.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true })),
  );
  expect(result.container.querySelector("aside")?.style.width).toBe("416px");
  const conversation = result.container.querySelector('[aria-label="resizeConversation"]');
  expect(conversation).not.toBeNull();
  await act(async () =>
    conversation!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true })),
  );
  expect((result.container.querySelector("[data-conversation]") as HTMLElement).style.height).toBe(
    "280px",
  );
  for (const viewport of [1024, 390]) {
    await act(async () => {
      window.innerWidth = viewport;
      window.dispatchEvent(new Event("resize"));
    });
    expect(result.container.querySelector("aside")).toBeNull();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("detail body");
  }
  await result.unmount();
  rect.mockRestore();
  window.innerWidth = original;
});
test("closing a detail sheet restores focus to its originating selection", async () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390, writable: true });
  function Focus() {
    const [selection, setSelection] = useState(parseSelection("run=r").selection);
    return (
      <WorkbenchShell
        selection={selection}
        onSelectionChange={setSelection}
        layout={{}}
        onLayoutChange={() => {}}
        view={
          <button data-origin onClick={() => setSelection({ ...selection, stepId: "s" })}>
            Select
          </button>
        }
        detail={<div>detail body</div>}
      />
    );
  }
  const result = await renderComponent(<Focus />);
  const origin = result.container.querySelector<HTMLButtonElement>("[data-origin]")!;
  await act(async () => {
    origin.focus();
    origin.click();
  });
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  await act(async () => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  await vi.waitFor(() => expect(document.activeElement).toBe(origin));
  await result.unmount();
});
test("mobile conversation uses a retained full-height mode and a 44px collapsed bar", async () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390, writable: true });
  const result = await renderComponent(<Probe />);
  const section = result.container.querySelector<HTMLElement>("[data-conversation]")!;
  expect(section.hidden).toBe(true);
  const bar = result.container.querySelector<HTMLElement>("[data-conversation-bar]")!;
  expect(bar.style.height).toBe("44px");
  const trigger = bar.querySelector("button")!;
  await act(async () => {
    trigger.focus();
    trigger.click();
  });
  expect(section.dataset.mode).toBe("full-height");
  expect(section.style.height).toBe("auto");
  expect(result.container.querySelector<HTMLElement>("[data-workspace]")?.style.display).toBe(
    "none",
  );
  expect(section.querySelector("input")?.value).toBe("draft");
  await act(async () =>
    [...section.querySelectorAll("button")].find((b) => b.textContent === "collapse")!.click(),
  );
  expect(section.hidden).toBe(true);
  expect(document.activeElement?.textContent).toContain("conversation");
  expect(section.querySelector("input")?.value).toBe("draft");
  await result.unmount();
});
test("active tabs control a labelled real tabpanel without remounting the draft", async () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440, writable: true });
  const result = await renderComponent(<Probe />);
  const draft = result.container.querySelector("input");
  for (const value of ["task", "debug"]) {
    const tab = result.container.querySelector<HTMLButtonElement>(
      `[role="tab"][data-state="${value === "task" ? "active" : "inactive"}"]`,
    )!;
    await act(async () => tab.click());
    const active = result.container.querySelector('[role="tab"][aria-selected="true"]')!;
    const panel = document.getElementById(active.getAttribute("aria-controls")!);
    expect(panel?.getAttribute("role")).toBe("tabpanel");
    expect(panel?.getAttribute("aria-labelledby")).toBe(active.id);
    expect(result.container.querySelector("input")).toBe(draft);
  }
  await result.unmount();
});
