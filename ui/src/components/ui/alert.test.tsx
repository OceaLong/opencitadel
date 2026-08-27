// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { Alert, AlertDescription, AlertTitle } from "./alert";

describe("Alert", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("renders role=alert with title and description", async () => {
    const { container, unmount } = await renderComponent(
      <Alert variant="approval">
        <AlertTitle>Approval needed</AlertTitle>
        <AlertDescription>Tool call awaits your decision.</AlertDescription>
      </Alert>,
    );
    const el = container.querySelector('[role="alert"]');
    expect(el?.className).toContain("border-l-accent-approval");
    expect(el?.className).toContain("bg-approval-subtle");
    expect(container.textContent).toContain("Approval needed");
    await unmount();
  });

  it("defaults to info tone", async () => {
    const { container, unmount } = await renderComponent(<Alert>plain</Alert>);
    const el = container.querySelector('[role="alert"]');
    expect(el?.className).toContain("bg-info-subtle");
    await unmount();
  });
});
