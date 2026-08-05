// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  readDocumentPage: vi.fn(),
}));

vi.mock("next-intl", () =>
  mockNextIntl({
    documentPageLoadMore: "Load more source",
    documentPageEmpty: "Empty",
    documentPageError: "Read failed",
    documentRevisionMismatch: "Revision mismatch",
  }),
);
vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: { readDocumentPage: mocks.readDocumentPage },
}));
vi.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/lib/icons", () => ({ IconLoading: () => <span>loading</span> }));

import { DocumentPager } from "./document-pager";

const document = {
  id: "doc1",
  kb_id: "kb1",
  title: "Guide",
  source_type: "upload",
  mime: "text/plain",
  page_count: 1,
  status: "ready",
};

async function renderPager(props?: Partial<React.ComponentProps<typeof DocumentPager>>) {
  const merged = {
    knowledgeBaseId: "kb1",
    versionId: "v1",
    documentId: "doc1",
    ...props,
  };
  const { container, root, unmount } = await renderComponent(<DocumentPager {...merged} />);
  return { container, root, unmount, merged };
}

const documentGlobal = globalThis.document;

describe("DocumentPager", () => {
  afterEach(() => {
    mocks.readDocumentPage.mockReset();
    documentGlobal.body.replaceChildren();
  });

  it("loads first/next/final pages, synchronously blocks double click, and dedupes chunk ids", async () => {
    let resolveNext!: (value: unknown) => void;
    mocks.readDocumentPage
      .mockResolvedValueOnce({
        document,
        document_revision_id: "r1",
        items: [
          { id: "c1", heading_path: "", ordinal: 0, content: "one" },
          { id: "c2", heading_path: "", ordinal: 1, content: "two" },
        ],
        next_cursor: "next",
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveNext = resolve;
          }),
      );
    const { container, unmount } = await renderPager();
    const button = container.querySelector("button") as HTMLButtonElement;

    act(() => {
      button.click();
      button.click();
    });
    expect(mocks.readDocumentPage).toHaveBeenCalledTimes(2);

    await act(async () => {
      resolveNext({
        document,
        document_revision_id: "r1",
        items: [
          { id: "c2", heading_path: "", ordinal: 1, content: "duplicate" },
          { id: "c3", heading_path: "", ordinal: 2, content: "three" },
        ],
        next_cursor: null,
      });
      await Promise.resolve();
    });

    expect(container.querySelectorAll("[data-chunk-id='c2']")).toHaveLength(1);
    expect(container.textContent).toContain("one");
    expect(container.textContent).toContain("three");
    expect(container.querySelector("button")).toBeNull();
    await unmount();
  });

  it("keeps loaded content and cursor when a next-page request fails", async () => {
    mocks.readDocumentPage
      .mockResolvedValueOnce({
        document,
        document_revision_id: "r1",
        items: [{ id: "c1", heading_path: "", ordinal: 0, content: "stable" }],
        next_cursor: "retry-cursor",
      })
      .mockRejectedValueOnce(new Error("network down"));
    const { container, unmount } = await renderPager();

    await act(async () => {
      (container.querySelector("button") as HTMLButtonElement).click();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("stable");
    expect(container.textContent).toContain("network down");
    expect(container.querySelector("button")).not.toBeNull();
    await unmount();
  });

  it("resets on identity change and ignores the old response", async () => {
    let resolveOld!: (value: unknown) => void;
    mocks.readDocumentPage
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve;
          }),
      )
      .mockResolvedValueOnce({
        document: { ...document, id: "doc2", title: "Second" },
        document_revision_id: "r2",
        items: [{ id: "new", heading_path: "", ordinal: 0, content: "new content" }],
        next_cursor: null,
      });
    const { container, root, unmount, merged } = await renderPager();

    await act(async () => {
      root.render(<DocumentPager {...merged} documentId="doc2" />);
      await Promise.resolve();
    });
    await act(async () => {
      resolveOld({
        document,
        document_revision_id: "r1",
        items: [{ id: "old", heading_path: "", ordinal: 0, content: "old content" }],
        next_cursor: null,
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("new content");
    expect(container.textContent).not.toContain("old content");
    await unmount();
  });
});
