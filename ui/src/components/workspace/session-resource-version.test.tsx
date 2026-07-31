// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { sessionApi } from "@/lib/api/session";
import type { SessionResourceBinding } from "@/lib/api/types";

import { SessionResourceVersion } from "./session-resource-version";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

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
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) =>
    ({
      upgradeContext: "升级上下文",
      confirmUpgrade: `确认升级到 ${values?.version ?? ""}`,
    })[key] ?? key,
}));

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
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
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
      await Promise.resolve();
    });

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
    await act(async () => root.unmount());
    document.body.replaceChildren();
  });
});
