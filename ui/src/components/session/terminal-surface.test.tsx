// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { TerminalSurface } from "./terminal-surface";

afterEach(() => {
  document.body.replaceChildren();
});

it("renders terminal tokens and optional title bar", async () => {
  const { container, unmount } = await renderComponent(
    <TerminalSurface title="bash">
      <pre>echo hi</pre>
    </TerminalSurface>,
  );
  const root = container.firstElementChild;
  expect(root?.className).toContain("bg-terminal");
  expect(root?.className).toContain("text-terminal-foreground");
  expect(root?.textContent).toContain("bash");
  expect(root?.firstElementChild?.className).toContain("bg-terminal-muted");
  await unmount();
});

it("omits title bar when no title", async () => {
  const { container, unmount } = await renderComponent(
    <TerminalSurface>
      <pre>x</pre>
    </TerminalSurface>,
  );
  expect(container.querySelector(".bg-terminal-muted")).toBeNull();
  await unmount();
});
