// @vitest-environment jsdom
import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/fetch";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import { DefinitionManagement } from "./definition-management";

const api = vi.hoisted(() => ({
  definitions: vi.fn(),
  workers: vi.fn(),
  head: vi.fn(),
  retire: vi.fn(),
  result: vi.fn(),
}));
vi.mock("@/lib/api/execution-definitions", () => ({ executionDefinitionsApi: api }));
const version = {
  family: "agent",
  definition_version: 2,
  manifest_digest: "b".repeat(64),
  head_digest: "a".repeat(64),
  is_head: false,
  retired_at: null,
};
let unmount: (() => Promise<void>) | undefined;
beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.definitions.mockResolvedValue({ items: [version], next_offset: null });
  api.workers.mockResolvedValue({ items: [], next_offset: null });
});
afterEach(async () => {
  await unmount?.();
  document.body.replaceChildren();
  vi.useRealTimers();
});
async function mount() {
  const rendered = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <DefinitionManagement workspaceId="owned" />
    </NextIntlClientProvider>,
  );
  unmount = rendered.unmount;
  const input = rendered.container.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
      input,
      "owned rollout",
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return rendered.container;
}
function button(container: HTMLElement, text: string) {
  return Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text)!;
}
it("waits for kernel completion and refreshes inventory", async () => {
  api.head.mockResolvedValue({ request_id: "owned", status: "pending", result: null });
  api.result.mockResolvedValue({ request_id: "owned", status: "completed", result: {} });
  const container = await mount();
  await act(async () => button(container, en.executionDefinitions.publish).click());
  expect(button(container, en.executionDefinitions.publish).disabled).toBe(true);
  expect(container.textContent).toContain(en.executionDefinitions.pending);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(api.head).toHaveBeenCalledTimes(1);
  expect(api.result).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain(en.executionDefinitions.completed);
  expect(api.definitions).toHaveBeenCalledTimes(2);
});
it("reuses the exact request after an uncertain transport result", async () => {
  api.head
    .mockRejectedValueOnce(new Error("connection interrupted"))
    .mockResolvedValue({ status: "completed", result: {} });
  const container = await mount();
  await act(async () => button(container, en.executionDefinitions.publish).click());
  const original = api.head.mock.calls[0][1];
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(api.head.mock.calls[1][1]).toEqual(original);
  expect(original.expected_digest).toBe(version.head_digest);
  expect(original.reason).toBe("owned rollout");
});
it("shows blocking Run links without reporting retirement success", async () => {
  api.retire.mockRejectedValue(
    new ApiError(409, "blocked", { references: { run_ids: ["owned-run"] } }),
  );
  const container = await mount();
  await act(async () => button(container, en.executionDefinitions.retire).click());
  expect(container.textContent).toContain(en.executionDefinitions.blocked);
  expect(container.querySelector('a[href="/runs/owned-run"]')).not.toBeNull();
  expect(container.textContent).not.toContain(en.executionDefinitions.completed);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(api.retire).toHaveBeenCalledTimes(1);
});
it("stops pending requests when the authorized view unmounts", async () => {
  api.head.mockResolvedValue({ status: "pending", result: null });
  const container = await mount();
  await act(async () => button(container, en.executionDefinitions.publish).click());
  const signal = api.head.mock.calls[0][2].signal;
  await unmount!();
  unmount = undefined;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(signal.aborted).toBe(true);
  expect(api.result).not.toHaveBeenCalled();
});
