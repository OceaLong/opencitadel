// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { PlaybackControls } from "./playback-controls";

test("keyboard-accessible adjacent event buttons preserve cursor identity and return live", async () => {
  const seek = vi.fn(),
    live = vi.fn();
  const { container, root } = await renderComponent(
    <PlaybackControls
      at="opaque"
      latestAvailable="2026-01-01T00:01:00Z"
      start="2026-01-01T00:00:00Z"
      currentTime="2026-01-01T00:00:10Z"
      coverage={{ state: "complete", missing_fields: [], missing_intervals: [] }}
      onSeekTime={vi.fn()}
      onSeekEvent={seek}
      onReturnLive={live}
    />,
  );
  const buttons = Array.from(container.querySelectorAll("button"));
  await act(async () => buttons.find((x) => x.textContent?.includes("previousEvent"))!.click());
  expect(seek).toHaveBeenCalledWith("before");
  await act(async () => buttons.find((x) => x.textContent?.includes("nextEvent"))!.click());
  expect(seek).toHaveBeenCalledWith("after");
  await act(async () => buttons.find((x) => x.textContent?.includes("returnLive"))!.click());
  expect(live).toHaveBeenCalledOnce();
  await act(async () => root.unmount());
});

test("focused playback arrows seek adjacent events and announce confirmed boundary", async () => {
  const seek = vi.fn();
  const result = await renderComponent(
    <PlaybackControls
      at="opaque"
      latestAvailable="2026-01-01T00:01:00Z"
      currentTime="2026-01-01T00:00:10Z"
      onSeekTime={vi.fn()}
      onSeekEvent={seek}
      onReturnLive={vi.fn()}
    />,
  );
  const previous = result.container.querySelector<HTMLButtonElement>(
    'button[aria-label="previousEvent"]',
  )!;
  await act(async () =>
    previous.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })),
  );
  expect(seek).toHaveBeenCalledWith("after");
  expect(result.container.querySelector('[aria-live="polite"]')?.textContent).toContain(
    "2026-01-01T00:00:10Z",
  );
  await result.unmount();
});
