// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SessionResourceBinding } from "@/lib/api/types";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

type ResourceVersionProps = {
  onBindingsChanged?: (bindings: SessionResourceBinding[]) => void;
};

const mocks = vi.hoisted(() => ({
  knowledgeProps: vi.fn(),
  resourceVersionProps: vi.fn(),
}));

vi.mock("next-intl", () => mockNextIntl());
vi.mock("@/components/workspace/knowledge-context-panel", () => ({
  KnowledgeContextPanel: (props: unknown) => {
    mocks.knowledgeProps(props);
    return <div>knowledge</div>;
  },
}));
vi.mock("@/components/workspace/codebase-context-panel", () => ({
  CodebaseContextPanel: () => <div>code</div>,
}));
vi.mock("@/components/workspace/session-resource-version", () => ({
  SessionResourceVersion: (props: unknown) => {
    mocks.resourceVersionProps(props);
    return null;
  },
}));
vi.mock("@/components/ui/tabs", () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { SessionContextPanel } from "./session-context-panel";

describe("SessionContextPanel", () => {
  afterEach(() => {
    mocks.knowledgeProps.mockReset();
    mocks.resourceVersionProps.mockReset();
    document.body.replaceChildren();
  });

  it("passes the current same-resource session binding to knowledge context", async () => {
    const { unmount } = await renderComponent(
      <SessionContextPanel
        knowledgeBaseId="kb1"
        resourceBindings={[
          {
            binding_id: "old",
            resource_kind: "knowledge_base",
            resource_id: "kb1",
            version_id: "v-old",
            is_current: false,
          },
          {
            binding_id: "current",
            resource_kind: "knowledge_base",
            resource_id: "kb1",
            version_id: "v-bound",
            is_current: true,
          },
        ]}
      />,
    );

    expect(mocks.knowledgeProps).toHaveBeenCalledWith(
      expect.objectContaining({
        knowledgeBaseId: "kb1",
        versionId: "v-bound",
      }),
    );
    await unmount();
  });

  it("switches knowledge context when the current binding refreshes", async () => {
    const { unmount } = await renderComponent(
      <SessionContextPanel
        sessionId="session-1"
        knowledgeBaseId="kb1"
        resourceBindings={[
          {
            binding_id: "binding-v1",
            resource_kind: "knowledge_base",
            resource_id: "kb1",
            version_id: "v1",
            is_current: true,
          },
        ]}
      />,
    );

    expect(mocks.knowledgeProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ versionId: "v1" }),
    );
    const versionProps = mocks.resourceVersionProps.mock.lastCall?.[0] as
      | ResourceVersionProps
      | undefined;
    expect(versionProps?.onBindingsChanged).toEqual(expect.any(Function));

    await act(async () => {
      versionProps?.onBindingsChanged?.([
        {
          binding_id: "binding-v2",
          resource_kind: "knowledge_base",
          resource_id: "kb1",
          version_id: "v2",
          is_current: true,
        },
      ]);
    });

    expect(mocks.knowledgeProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ versionId: "v2" }),
    );
    await unmount();
  });
});
