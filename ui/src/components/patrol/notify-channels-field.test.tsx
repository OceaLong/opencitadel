// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const api = vi.hoisted(() => ({
  validateChannel: vi.fn(),
  testChannel: vi.fn(),
  deliveries: vi.fn(),
}));
vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));
vi.mock("@/lib/api/notifications", () => ({ notificationsApi: api }));
import { emptyNotifyChannel, NotifyChannelsField } from "./notify-channels-field";

afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});

it("validates without sending and sends only after the explicit test action", async () => {
  api.validateChannel.mockResolvedValue({ valid: true, sent: false });
  api.testChannel.mockResolvedValue({ delivery_id: "delivery-1", status: "pending" });
  const channel = { ...emptyNotifyChannel(), type: "email" as const, address: "a@example.com" };
  const rendered = await renderComponent(
    <NotifyChannelsField value={[channel]} servers={[]} onChange={() => {}} />,
  );
  const find = (label: string) =>
    [...rendered.container.querySelectorAll("button")].find(
      (button) => button.textContent === label,
    )!;
  await act(async () => {
    find("Validate configuration (no send)").click();
  });
  expect(api.validateChannel).toHaveBeenCalledWith(channel);
  expect(api.testChannel).not.toHaveBeenCalled();
  expect(rendered.container.textContent).toContain("Configuration valid; no message sent");
  await act(async () => {
    find("Send test notification").click();
  });
  expect(api.testChannel).toHaveBeenCalledWith(channel, expect.any(String));
  expect(rendered.container.textContent).toContain("Test delivery: pending");
  await rendered.unmount();
});
