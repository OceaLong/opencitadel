// @vitest-environment jsdom

import { act, createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  graphProps: vi.fn(),
  pagerProps: vi.fn(),
}));

vi.mock("next-intl", () => mockNextIntl());
vi.mock("@/components/knowledge/knowledge-graph", () => ({
  KnowledgeGraph: (props: unknown) => {
    mocks.graphProps(props);
    return <div>real graph</div>;
  },
}));
vi.mock("@/components/knowledge/document-pager", () => ({
  DocumentPager: (props: unknown) => {
    mocks.pagerProps(props);
    return <div>exact document</div>;
  },
}));
vi.mock("@/components/ui/tabs", () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { KnowledgeContextPanel } from "./knowledge-context-panel";

describe("KnowledgeContextPanel", () => {
  afterEach(() => {
    mocks.graphProps.mockReset();
    mocks.pagerProps.mockReset();
    document.body.replaceChildren();
  });

  it("uses the bound version for graph and preserves citation version/revision for paging", async () => {
    const sourceRef = createRef<((value: string) => void) | null>();
    const { container, unmount } = await renderComponent(
      <KnowledgeContextPanel knowledgeBaseId="kb1" versionId="v7" onSourceClickRef={sourceRef} />,
    );

    expect(mocks.graphProps).toHaveBeenCalledWith({
      knowledgeBaseId: "kb1",
      versionId: "v7",
    });

    act(() => {
      sourceRef.current?.("kbdoc://doc1?page=3&version=v7&revision=r9&chunk=c4");
    });
    expect(mocks.pagerProps).toHaveBeenLastCalledWith({
      knowledgeBaseId: "kb1",
      versionId: "v7",
      documentId: "doc1",
      page: 3,
      expectedRevisionId: "r9",
    });

    act(() => {
      sourceRef.current?.("kbdoc://doc2?version=v8&revision=r10");
    });
    expect(container.querySelector("[role='alert']")).not.toBeNull();
    expect(mocks.pagerProps).toHaveBeenLastCalledWith({
      knowledgeBaseId: "kb1",
      versionId: "v7",
      documentId: "doc1",
      page: 3,
      expectedRevisionId: "r9",
    });
    await unmount();
  });
});
