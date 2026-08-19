// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SSEEventData } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, unknown>) => {
    if (key === "errorsTitle") return `Errors (${values?.count ?? 0})`;
    if (key === "rawError") return "Raw error";
    return key;
  },
}));

import { SessionDebugSheet } from "./session-debug-sheet";

const rawError = "<Token var=<ContextVar name='task_id'>> was created in a different Context";

function repeatedErrors(count: number): SSEEventData[] {
  return Array.from({ length: count }, (_, index) => ({
    type: "error" as const,
    data: {
      error: rawError,
      code: "MODEL_UNAVAILABLE",
      event_id: String(index + 1),
      schema_version: 2 as const,
      visibility: "user" as const,
      channel: "ui" as const,
      persist: true,
      created_at: index + 1,
    },
  }));
}

async function openSheet() {
  const button = document.querySelector("button");
  expect(button).toBeTruthy();
  await act(async () => {
    button?.click();
    await Promise.resolve();
  });
}

describe("SessionDebugSheet error evidence", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("shows the total occurrence count instead of the group count", async () => {
    const { container, unmount } = await renderComponent(
      <SessionDebugSheet events={repeatedErrors(100)} compact />,
    );

    expect(container.querySelector("button")?.textContent).toContain("99+");
    await openSheet();
    expect(document.body.textContent).toContain("Errors (100)");

    await unmount();
  });

  it("shows the raw backend error when localization differs", async () => {
    const { unmount } = await renderComponent(
      <SessionDebugSheet events={repeatedErrors(1)} compact />,
    );

    await openSheet();
    expect(document.body.textContent).toContain("Raw error");
    expect(document.body.textContent).toContain(rawError);

    await unmount();
  });
});
