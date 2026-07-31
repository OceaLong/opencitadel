// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { sessionApi } from "@/lib/api/session";
import type { SessionResourceBinding } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ knowledgeProps: vi.fn() }));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) =>
    ({
      upgradeContext: "Upgrade context",
      confirmUpgrade: `Confirm ${values?.version ?? ""}`,
    })[key] ?? key,
}));
vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getResourceBindings: vi.fn(),
    getAvailableResourceVersions: vi.fn(),
    upgradeResourceBinding: vi.fn(),
  },
}));
vi.mock("@/components/workspace/knowledge-context-panel", () => ({
  KnowledgeContextPanel: (props: unknown) => {
    mocks.knowledgeProps(props);
    return <div>knowledge</div>;
  },
}));
vi.mock("@/components/workspace/codebase-context-panel", () => ({
  CodebaseContextPanel: () => <div>code</div>,
}));
vi.mock("@/components/ui/tabs", () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { SessionContextPanel } from "./session-context-panel";

describe("SessionContextPanel resource upgrade", () => {
  beforeAll(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    vi.mocked(sessionApi.getResourceBindings).mockReset();
    vi.mocked(sessionApi.getAvailableResourceVersions).mockReset();
    vi.mocked(sessionApi.upgradeResourceBinding).mockReset();
    mocks.knowledgeProps.mockReset();
    document.body.replaceChildren();
  });

  it("moves the knowledge panel to the refreshed current binding", async () => {
    const bindingV1: SessionResourceBinding = {
      binding_id: "binding-v1",
      resource_kind: "knowledge_base",
      resource_id: "kb1",
      version_id: "v1",
      is_current: true,
    };
    const bindingV2: SessionResourceBinding = {
      binding_id: "binding-v2",
      resource_kind: "knowledge_base",
      resource_id: "kb1",
      version_id: "v2",
      is_current: true,
    };
    vi.mocked(sessionApi.getResourceBindings)
      .mockResolvedValueOnce([bindingV1])
      .mockResolvedValueOnce([bindingV2]);
    vi.mocked(sessionApi.getAvailableResourceVersions).mockResolvedValue([bindingV1, bindingV2]);
    vi.mocked(sessionApi.upgradeResourceBinding).mockResolvedValue({
      old_binding_id: bindingV1.binding_id,
      new_binding_id: bindingV2.binding_id,
      current_version_id: bindingV2.version_id,
    });

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <SessionContextPanel
          sessionId="session-1"
          knowledgeBaseId="kb1"
          resourceBindings={[bindingV1]}
        />,
      );
      await Promise.resolve();
    });
    expect(mocks.knowledgeProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ versionId: "v1" }),
    );

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "Upgrade context")
        ?.click();
    });
    await act(async () => {
      Array.from(document.querySelectorAll("button"))
        .find((button) => button.textContent === "Confirm v2")
        ?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(sessionApi.upgradeResourceBinding).toHaveBeenCalledWith(
      "session-1",
      "knowledge_base",
      "v2",
    );
    expect(mocks.knowledgeProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ versionId: "v2" }),
    );
    await act(async () => root.unmount());
  });
});
