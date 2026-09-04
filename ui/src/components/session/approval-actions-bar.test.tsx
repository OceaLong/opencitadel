// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApprovalEventData } from "@/lib/api/types";

import { mockNextIntl, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

vi.mock("next-intl", () =>
  mockNextIntl({
    toolConfirmTitle: "Tool action requires approval",
    approve: "Approve",
    reject: "Reject",
    unknownTool: "Unknown tool",
    cancel: "Cancel",
  }),
);
vi.mock("sonner", () => mockSonner());

import { ApprovalActionsBar } from "./approval-actions-bar";

function makeApproval(overrides: Partial<ApprovalEventData> = {}): ApprovalEventData {
  return {
    approval_id: "appr-1",
    kind: "tool",
    payload: { tool_name: "run_shell", note: "This command mutates the workspace" },
    options: ["approve", "reject"],
    schema_version: 1,
    visibility: "user",
    channel: "ui",
    persist: true,
    created_at: 1725400000,
    ...overrides,
  };
}

function buttonsOf(container: HTMLElement): HTMLButtonElement[] {
  return [...container.querySelectorAll("button")];
}

afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
});

describe("ApprovalActionsBar", () => {
  it("renders the tool approval prompt with approve and reject buttons", async () => {
    const { container, unmount } = await renderComponent(
      <ApprovalActionsBar approval={makeApproval()} onSend={vi.fn()} />,
    );

    expect(container.textContent).toContain("Tool action requires approval");
    expect(container.textContent).toContain("run_shell");
    expect(container.textContent).toContain("This command mutates the workspace");
    const labels = buttonsOf(container).map((button) => button.textContent);
    expect(labels).toEqual(["Approve", "Reject"]);
    await unmount();
  });

  it("clicking approve sends the approve decision", async () => {
    const onSend = vi.fn();
    const { container, unmount } = await renderComponent(
      <ApprovalActionsBar approval={makeApproval()} onSend={onSend} />,
    );

    await act(async () => {
      buttonsOf(container)
        .find((button) => button.textContent === "Approve")!
        .click();
    });

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("approve", undefined);
    await unmount();
  });

  it("disables the decision buttons when disabled", async () => {
    const { container, unmount } = await renderComponent(
      <ApprovalActionsBar approval={makeApproval()} onSend={vi.fn()} disabled />,
    );

    expect(buttonsOf(container).every((button) => button.disabled)).toBe(true);
    await unmount();
  });
});
