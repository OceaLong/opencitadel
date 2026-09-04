// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

vi.mock("next-intl", () =>
  mockNextIntl({
    clarificationTitle: "Your choice is needed",
    clarificationDecline: "Decline",
    sendFailed: "Failed to send response",
  }),
);
vi.mock("sonner", () => mockSonner());

import { ClarificationCard } from "./clarification-card";

const baseProps = {
  question: "Which environment should I target?",
  choices: ["Staging", "Production"],
};

function buttonsOf(container: HTMLElement): HTMLButtonElement[] {
  return [...container.querySelectorAll("button")];
}

afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
});

describe("ClarificationCard", () => {
  it("renders the question and one button per choice plus a decline button", async () => {
    const { container, unmount } = await renderComponent(
      <ClarificationCard {...baseProps} onChoose={vi.fn()} onDecline={vi.fn()} />,
    );

    expect(container.textContent).toContain("Your choice is needed");
    expect(container.textContent).toContain("Which environment should I target?");
    const labels = buttonsOf(container).map((button) => button.textContent);
    expect(labels).toEqual(["Staging", "Production", "Decline"]);
    await unmount();
  });

  it("clicking a choice calls onChoose with the choice text", async () => {
    const onChoose = vi.fn();
    const onDecline = vi.fn();
    const { container, unmount } = await renderComponent(
      <ClarificationCard {...baseProps} onChoose={onChoose} onDecline={onDecline} />,
    );

    await act(async () => {
      buttonsOf(container)
        .find((button) => button.textContent === "Production")!
        .click();
    });

    expect(onChoose).toHaveBeenCalledTimes(1);
    expect(onChoose).toHaveBeenCalledWith("Production");
    expect(onDecline).not.toHaveBeenCalled();
    await unmount();
  });

  it("clicking decline calls onDecline", async () => {
    const onChoose = vi.fn();
    const onDecline = vi.fn();
    const { container, unmount } = await renderComponent(
      <ClarificationCard {...baseProps} onChoose={onChoose} onDecline={onDecline} />,
    );

    await act(async () => {
      buttonsOf(container)
        .find((button) => button.textContent === "Decline")!
        .click();
    });

    expect(onDecline).toHaveBeenCalledTimes(1);
    expect(onChoose).not.toHaveBeenCalled();
    await unmount();
  });

  it("disables all buttons when disabled", async () => {
    const { container, unmount } = await renderComponent(
      <ClarificationCard {...baseProps} onChoose={vi.fn()} onDecline={vi.fn()} disabled />,
    );

    expect(buttonsOf(container).every((button) => button.disabled)).toBe(true);
    await unmount();
  });

  it("disables all buttons while a decision is in flight", async () => {
    let resolveChoose: () => void = () => {};
    const onChoose = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveChoose = resolve;
        }),
    );
    const { container, unmount } = await renderComponent(
      <ClarificationCard {...baseProps} onChoose={onChoose} onDecline={vi.fn()} />,
    );

    await act(async () => {
      buttonsOf(container)[0].click();
    });
    expect(buttonsOf(container).every((button) => button.disabled)).toBe(true);

    await act(async () => {
      resolveChoose();
    });
    expect(buttonsOf(container).every((button) => button.disabled)).toBe(false);
    await unmount();
  });
});
