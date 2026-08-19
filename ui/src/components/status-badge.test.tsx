// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { renderComponent } from "@/test-utils/render";
import { StatusBadge, type StatusBadgeVariant } from "./status-badge";

const ALL_VARIANTS: StatusBadgeVariant[] = [
  "default",
  "secondary",
  "destructive",
  "outline",
  "success",
  "warning",
  "info",
];

describe("StatusBadge", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("renders every variant through the same span path", async () => {
    for (const variant of ALL_VARIANTS) {
      const { container, unmount } = await renderComponent(
        <StatusBadge variant={variant}>x</StatusBadge>,
      );
      const el = container.firstElementChild;
      expect(el?.tagName, variant).toBe("SPAN");
      expect(el?.className, variant).toContain("rounded-full");
      expect(el?.className, variant).toContain("px-2.5");
      await unmount();
    }
  });

  it("warning variant uses text + thin border, no background block", async () => {
    const { container, unmount } = await renderComponent(
      <StatusBadge variant="warning">w</StatusBadge>,
    );
    const cls = container.firstElementChild?.className ?? "";
    expect(cls).toContain("bg-transparent");
    expect(cls).toContain("text-warning");
    await unmount();
  });

  it("forwards data-testid and className", async () => {
    const { container, unmount } = await renderComponent(
      <StatusBadge data-testid="sb" className="ml-2">
        ok
      </StatusBadge>,
    );
    const el = container.querySelector('[data-testid="sb"]');
    expect(el?.className).toContain("ml-2");
    await unmount();
  });
});
