// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const api = vi.hoisted(() => ({ deliveries: vi.fn(), retryDelivery: vi.fn() }));
vi.mock("next-intl", () => ({ useLocale: () => "en" }));
vi.mock("@/lib/api/notifications", () => ({ notificationsApi: api }));
import { NotificationDeliveries } from "./notification-deliveries";

afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});

it("shows failed delivery and retries that delivery without rerunning the job", async () => {
  const row = {
    id: "delivery-1",
    channel_type: "email",
    message: "Job complete",
    status: "failed",
    attempts: 5,
    last_error: "Unavailable",
    next_attempt_at: "2026-09-07T00:00:00Z",
  };
  api.deliveries.mockResolvedValueOnce({ deliveries: [row] }).mockResolvedValue({
    deliveries: [{ ...row, status: "pending", attempts: 0, last_error: null }],
  });
  api.retryDelivery.mockResolvedValue({ retried: true });
  const rendered = await renderComponent(<NotificationDeliveries />);
  expect(rendered.container.textContent).toContain("Failed");
  const button = [...rendered.container.querySelectorAll("button")].find(
    (button) => button.textContent === "Retry delivery",
  )!;
  await act(async () => {
    button.click();
  });
  expect(api.retryDelivery).toHaveBeenCalledWith("delivery-1");
  expect(rendered.container.textContent).toContain("Pending");
  expect(rendered.container.textContent).not.toContain("Retry delivery");
  await rendered.unmount();
});
