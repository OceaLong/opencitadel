// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({ getGraph: vi.fn() }));

vi.mock("next-intl", () =>
  mockNextIntl({
    graphLoading: "Loading",
    graphLoadError: "Load failed",
    graphUnavailable: "Graph unavailable",
    graphEmpty: "Graph empty",
    graphRelations: "Relations",
  }),
);
vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: { getGraph: mocks.getGraph },
}));
vi.mock("@/lib/icons", () => ({ IconLoading: () => <span>loading</span> }));

import { KnowledgeGraph } from "./knowledge-graph";

async function renderGraph() {
  return renderComponent(<KnowledgeGraph knowledgeBaseId="kb1" versionId="v1" />);
}

describe("KnowledgeGraph", () => {
  afterEach(() => {
    mocks.getGraph.mockReset();
    document.body.replaceChildren();
  });

  it("renders real entity nodes and relation edges", async () => {
    const aliceId = "11111111-1111-4111-8111-111111111111";
    const projectId = "22222222-2222-4222-8222-222222222222";
    mocks.getGraph.mockResolvedValue({
      capability: true,
      nodes: [
        { id: aliceId, name: "Alice", type: "person", description: "" },
        { id: projectId, name: "Citadel", type: "project", description: "" },
      ],
      edges: [
        {
          id: "works",
          source: aliceId,
          target: projectId,
          relation: "works on",
          evidence: [
            {
              version_id: "v1",
              document_revision_id: "r1",
              doc_id: "doc1",
              page_no: 2,
              chunk_id: "c1",
            },
          ],
        },
      ],
      next_cursor: null,
    });
    const { container, unmount } = await renderGraph();

    expect(
      container.querySelector(`[data-testid='knowledge-node-${aliceId}']`)?.textContent,
    ).toContain("Alice");
    const edge = container.querySelector("[data-testid='knowledge-edge-works']");
    expect(edge?.getAttribute("data-source")).toBe(aliceId);
    expect(edge?.getAttribute("data-target")).toBe(projectId);
    expect(edge?.textContent).toContain("Alice works on Citadel");
    expect(edge?.textContent).not.toContain(aliceId);
    expect(edge?.textContent).not.toContain(projectId);
    const evidence = edge?.querySelector("[data-document='doc1']");
    expect(evidence?.getAttribute("data-revision")).toBe("r1");
    await unmount();
  });

  it.each([
    [{ capability: false, nodes: [], edges: [] }, "Graph unavailable"],
    [{ capability: true, nodes: [], edges: [] }, "Graph empty"],
  ])("renders honest capability and empty states", async (response, expected) => {
    mocks.getGraph.mockResolvedValue(response);
    const { container, unmount } = await renderGraph();
    expect(container.textContent).toContain(expected);
    expect(container.querySelector("[data-testid^='knowledge-node-']")).toBeNull();
    await unmount();
  });

  it("renders an API error without synthesizing a graph", async () => {
    mocks.getGraph.mockRejectedValue(new Error("graph backend down"));
    const { container, unmount } = await renderGraph();
    expect(container.textContent).toContain("graph backend down");
    expect(container.querySelector("[data-testid^='knowledge-node-']")).toBeNull();
    await unmount();
  });
});
