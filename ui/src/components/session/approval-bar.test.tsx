// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { ApprovalBar } from "./approval-bar";

afterEach(() => {
  document.body.replaceChildren();
});

it("defaults to gate tone with left accent and pulse", async () => {
  const { container, unmount } = await renderComponent(<ApprovalBar>x</ApprovalBar>);
  const el = container.firstElementChild;
  expect(el?.className).toContain("bg-gate-subtle");
  expect(el?.className).toContain("border-l-accent-gate");
  expect(el?.className).toContain("border-l-4");
  expect(el?.className).toContain("animate-gate-pulse");
  await unmount();
});

it("info tone has no pulse and info accent", async () => {
  const { container, unmount } = await renderComponent(
    <ApprovalBar tone="info">x</ApprovalBar>,
  );
  const el = container.firstElementChild;
  expect(el?.className).toContain("bg-info-subtle");
  expect(el?.className).toContain("border-l-accent-info");
  expect(el?.className).not.toContain("animate-gate-pulse");
  await unmount();
});
