// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  capability: vi.fn(),
  openSettings: vi.fn(),
}));

vi.mock("@/hooks/use-capabilities", () => ({
  useCapabilities: () => ({ capability: mocks.capability }),
}));
vi.mock("@/providers/settings-dialog-provider", () => ({
  useSettingsDialog: () => ({ openSettings: mocks.openSettings }),
}));

import { InferenceCapabilityNotice } from "./inference-capability-notice";

afterEach(() => {
  document.body.replaceChildren();
  mocks.capability.mockReset();
});

describe("InferenceCapabilityNotice", () => {
  it("guides an enabled vector consumer to inference settings when its binding is missing", async () => {
    mocks.capability.mockReturnValue({ state: "not_configured" });

    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={en}>
        <InferenceCapabilityNotice capabilityName="embeddings" enabled />
      </NextIntlClientProvider>,
    );

    expect(container.textContent).toContain(en.settingsInference.embeddingCapabilityUnavailable);
    expect(container.textContent).toContain(en.settingsInference.configureInference);
    await unmount();
  });

  it("does not treat an intentionally disabled vector consumer as missing configuration", async () => {
    mocks.capability.mockReturnValue({ state: "disabled" });

    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={en}>
        <InferenceCapabilityNotice capabilityName="embeddings" enabled />
      </NextIntlClientProvider>,
    );

    expect(container.textContent).toBe("");
    await unmount();
  });
});
