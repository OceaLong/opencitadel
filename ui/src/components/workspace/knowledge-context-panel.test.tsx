// @vitest-environment jsdom

import { act, createRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  graphProps: vi.fn(),
  pagerProps: vi.fn(),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) =>
    values ? `${key}:${values.version}` : key,
}));
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
  beforeAll(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(() => {
    mocks.graphProps.mockReset();
    mocks.pagerProps.mockReset();
    document.body.replaceChildren();
  });

  it("uses the bound version for graph and preserves citation version/revision for paging", async () => {
    const sourceRef = createRef<((value: string) => void) | null>();
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <KnowledgeContextPanel knowledgeBaseId="kb1" versionId="v7" onSourceClickRef={sourceRef} />,
      );
      await Promise.resolve();
    });

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
    await act(async () => root.unmount());
  });
});
