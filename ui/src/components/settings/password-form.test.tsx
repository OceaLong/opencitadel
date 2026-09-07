// @vitest-environment jsdom
import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import { PasswordForm } from "./password-form";

afterEach(() => document.body.replaceChildren());

async function fill(container: HTMLElement, name: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("blocks mismatched confirmation and clears secrets after successful change", async () => {
  const submit = vi.fn().mockResolvedValue(undefined);
  const { container, unmount } = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <PasswordForm onSubmit={submit} />
    </NextIntlClientProvider>,
  );
  await fill(container, "current-password", "old-password");
  await fill(container, "new-password", "new-password");
  await fill(container, "confirm-password", "different-password");
  await act(async () =>
    container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(submit).not.toHaveBeenCalled();
  expect(container.querySelector('[role="alert"]')?.textContent).toBeTruthy();
  await fill(container, "confirm-password", "new-password");
  await act(async () =>
    container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(submit).toHaveBeenCalledWith("new-password", "old-password");
  expect(Array.from(container.querySelectorAll("input")).every((input) => input.value === "")).toBe(
    true,
  );
  await unmount();
});

it("admin reset does not request the target's current password and shows failures", async () => {
  const submit = vi.fn().mockRejectedValue(new Error("Reset failed"));
  const { container, unmount } = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <PasswordForm adminReset onSubmit={submit} />
    </NextIntlClientProvider>,
  );
  expect(container.querySelector('[name="current-password"]')).toBeNull();
  await fill(container, "new-password", "new-password");
  await fill(container, "confirm-password", "new-password");
  await act(async () =>
    container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(container.querySelector('[role="alert"]')?.textContent).toBe("Reset failed");
  await unmount();
});
