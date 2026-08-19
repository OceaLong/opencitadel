// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import {
  executionStatusToRailState,
  GovernanceRail,
  GovernanceRailItem,
  toolEventRailState,
} from "./governance-rail";

afterEach(() => {
  document.body.replaceChildren();
});

it("maps tool status to rail state", () => {
  expect(toolEventRailState(undefined)).toBe("declared");
  expect(toolEventRailState("calling")).toBe("running");
  expect(toolEventRailState("called")).toBe("done");
  expect(toolEventRailState("error")).toBe("failed");
});

it("renders glyphs per state and rail line", async () => {
  const { container, unmount } = await renderComponent(
    <GovernanceRail>
      <GovernanceRailItem state="done">a</GovernanceRailItem>
      <GovernanceRailItem state="failed">b</GovernanceRailItem>
    </GovernanceRail>,
  );
  expect(container.querySelector('[data-rail-state="done"]')).toBeTruthy();
  expect(container.querySelector('[data-rail-state="failed"]')?.className).toContain("bg-destructive");
  expect(container.querySelector("[data-rail-line]")).toBeTruthy();
  await unmount();
});

it("dims rail line to bg-primary/40 when completed", async () => {
  const { container, unmount } = await renderComponent(
    <GovernanceRail lineState="completed">
      <GovernanceRailItem state="done">a</GovernanceRailItem>
    </GovernanceRail>,
  );
  expect(container.querySelector("[data-rail-line]")?.className).toContain("bg-primary/40");
  await unmount();
});

it("reddens rail line to bg-destructive/40 when failed", async () => {
  const { container, unmount } = await renderComponent(
    <GovernanceRail lineState="failed">
      <GovernanceRailItem state="failed">a</GovernanceRailItem>
    </GovernanceRail>,
  );
  expect(container.querySelector("[data-rail-line]")?.className).toContain("bg-destructive/40");
  await unmount();
});

it("renders checkpoint button when handler provided", async () => {
  const onRestore = vi.fn();
  const { container, unmount } = await renderComponent(
    <GovernanceRail checkpointTitle="restore" onRestoreCheckpoint={onRestore}>
      <GovernanceRailItem state="declared">a</GovernanceRailItem>
    </GovernanceRail>,
  );
  const btn = container.querySelector("button[data-rail-checkpoint]");
  expect(btn).toBeTruthy();
  (btn as HTMLButtonElement).click();
  expect(onRestore).toHaveBeenCalledOnce();
  await unmount();
});

it("maps execution status to rail state", () => {
  expect(executionStatusToRailState("completed")).toBe("done");
  expect(executionStatusToRailState("failed")).toBe("failed");
  expect(executionStatusToRailState("running")).toBe("running");
  expect(executionStatusToRailState("pending")).toBe("declared");
});

it("renders custom glyph and highlight", async () => {
  const { container, unmount } = await renderComponent(
    <GovernanceRail>
      <GovernanceRailItem state="done" glyph={<i data-custom-glyph />} highlight>
        x
      </GovernanceRailItem>
    </GovernanceRail>,
  );
  expect(container.querySelector("[data-custom-glyph]")).toBeTruthy();
  const glyphWrapper = container.querySelector('[data-rail-state="done"]');
  expect(glyphWrapper).toBeTruthy();
  expect(container.textContent).toContain("x");
  // Highlight must tint the row root (glyph + content together), not just the content div.
  expect(glyphWrapper?.parentElement?.className).toContain("bg-primary/5");
  await unmount();
});
