// @vitest-environment jsdom

import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { sessionApi } from "@/lib/api/session";
import type { SessionResourceBinding } from "@/lib/api/types";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

import { SessionResourceVersion } from "./session-resource-version";

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getResourceBindings: vi.fn(),
    getAvailableResourceVersions: vi.fn(),
    upgradeResourceBinding: vi.fn().mockResolvedValue({
      old_binding_id: "b1",
      new_binding_id: "b2",
      current_version_id: "v2",
    }),
  },
}));
vi.mock("next-intl", () =>
  mockNextIntl({
    upgradeContext: "升级上下文",
    confirmUpgrade: (values) => `确认升级到 ${values?.version ?? ""}`,
  }),
);

describe("SessionResourceVersion", () => {
  beforeEach(() => {
    vi.mocked(sessionApi.getResourceBindings).mockReset();
    vi.mocked(sessionApi.getAvailableResourceVersions).mockReset();
    vi.mocked(sessionApi.upgradeResourceBinding).mockClear();
  });

  it("upgrades only the current binding and labels historical messages", async () => {
    const bindingV1: SessionResourceBinding = {
      binding_id: "b1",
      resource_kind: "knowledge_base",
      resource_id: "kb1",
      version_id: "v1",
      is_current: true,
    };
    const bindingV2: SessionResourceBinding = {
      binding_id: "b2",
      resource_kind: "knowledge_base",
      resource_id: "kb1",
      version_id: "v2",
      is_current: true,
    };
    vi.mocked(sessionApi.getResourceBindings)
      .mockResolvedValueOnce([bindingV1])
      .mockResolvedValueOnce([bindingV2]);
    vi.mocked(sessionApi.getAvailableResourceVersions).mockResolvedValue([bindingV1, bindingV2]);
    const onBindingsChanged = vi.fn();
    const { container, unmount } = await renderComponent(
      <SessionResourceVersion
        sessionId="s1"
        bindings={[bindingV1]}
        versions={[
          { resource_kind: "knowledge_base", version_id: "v1" },
          { resource_kind: "knowledge_base", version_id: "v2" },
        ]}
        onBindingsChanged={onBindingsChanged}
        historicalMessages={[
          {
            id: "old",
            resource_bindings: [
              {
                binding_id: "b0",
                resource_kind: "knowledge_base",
                resource_id: "kb1",
                version_id: "v1",
              },
            ],
          },
        ]}
      />,
    );

    expect(container.textContent).toContain("v1");
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "升级上下文")
        ?.click();
    });
    expect(sessionApi.upgradeResourceBinding).not.toHaveBeenCalled();
    await act(async () => {
      Array.from(document.querySelectorAll("button"))
        .find((button) => button.textContent === "确认升级到 v2")
        ?.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(sessionApi.upgradeResourceBinding).toHaveBeenCalledWith("s1", "knowledge_base", "v2");
    expect(onBindingsChanged).toHaveBeenLastCalledWith([bindingV2]);
    expect(container.textContent).toContain("knowledge_base: v2");
    expect(container.querySelector("[data-testid=message-old]")?.textContent).toContain("v1");
    await unmount();
    document.body.replaceChildren();
  });
});
