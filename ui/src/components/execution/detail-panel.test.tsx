// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { StepDetail, ViewPage } from "@/lib/api/types/execution-view";
import { parseSelection } from "@/lib/execution-view/url-state";

import { renderComponent } from "@/test-utils/render";

import { DetailPanel } from "./detail-panel";
const scope = vi.hoisted(() => ({ revision: 1 }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    scope: { userId: "u", workspaceId: "w" },
    scopeRevision: scope.revision,
  }),
}));
vi.mock("@/components/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <p>{content}</p>,
}));
const step = {
  step_id: "step",
  run_id: "r",
  attempt_id: "attempt-1",
  activity_id: "activity",
  at: "cut",
  projection_revision: 1,
  kind: "tool",
  status: "unknown",
  public_summary: "Public summary",
  input_ref: { content_id: "i", availability: "available" },
  output_ref: { content_id: "o", availability: "available" },
  artifact_refs: [],
  citation_refs: [],
  completeness: { state: "complete" },
} as unknown as StepDetail;
const page = {
  run: { run_id: "r", public_summary: "Run summary" },
  at: "cut",
  revision: 1,
  steps: [step],
  next_cursor: null,
  approvals: [],
  artifacts: [],
} as unknown as ViewPage;
const selection = parseSelection("run=r&step=step&panel=input-output").selection;
const response = (content: string, extra = {}) => ({
  availability: "available" as const,
  content,
  content_type: "application/json",
  redacted: false,
  truncated: false,
  at: "cut",
  ...extra,
});
function component(extra: Partial<React.ComponentProps<typeof DetailPanel>> = {}) {
  return (
    <DetailPanel
      selection={selection}
      run={page.run}
      page={page}
      detail={step}
      onReturnLive={() => {}}
      onChanged={() => {}}
      onRevoked={() => {}}
      {...extra}
    />
  );
}
async function click(container: HTMLElement, text: string) {
  await act(async () => {
    const b = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
    expect(b, `button ${text}`).toBeDefined();
    b!.click();
  });
}
afterEach(() => {
  vi.restoreAllMocks();
  scope.revision = 1;
  document.body.replaceChildren();
});
test("default summaries never fetch; explicit 64KiB pages keep exact cut and show incomplete JSON safely", async () => {
  const read = vi
    .spyOn(executionViewApi, "readContent")
    .mockResolvedValueOnce(response('{"answer":', { truncated: true, next_cursor: "next" }))
    .mockResolvedValueOnce(response("42}"));
  const r = await renderComponent(component());
  expect(read).not.toHaveBeenCalled();
  expect(r.container.textContent).toContain("Public summary");
  expect(r.container.textContent).toContain("unknownOutcome");
  expect(r.container.textContent).not.toContain("retryCommand");
  await click(r.container, "loadInput");
  expect(read).toHaveBeenLastCalledWith(
    "r",
    "step",
    { at: "cut", content_kind: "input", limit_bytes: 65536, cursor: undefined },
    expect.anything(),
  );
  expect(r.container.textContent).toContain('{"answer":');
  expect(r.container.textContent).toContain("truncated");
  await click(r.container, "nextPage");
  expect(read.mock.calls[1][2].cursor).toBe("next");
  expect(JSON.parse(r.container.querySelector("pre")!.textContent!)).toEqual({ answer: 42 });
  await r.unmount();
});
test("scope or exposed authority loss clears pages and ignores a late body and download", async () => {
  let resolve!: (v: ReturnType<typeof response>) => void;
  let blob!: (v: Blob) => void;
  vi.spyOn(executionViewApi, "readContent").mockReturnValue(new Promise((r) => (resolve = r)));
  vi.spyOn(executionViewApi, "downloadContent").mockReturnValue(new Promise((r) => (blob = r)));
  const create = vi.fn(() => "blob:test");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const r = await renderComponent(component());
  await click(r.container, "loadInput");
  await click(r.container, "downloadOutput");
  await act(async () => r.root.render(component({ page: null, run: null, detail: null })));
  await act(async () => {
    resolve(response("late private body"));
    blob(new Blob(["late download"]));
  });
  expect(r.container.textContent).not.toContain("late private body");
  expect(r.container.textContent).not.toContain("Public summary");
  expect(create).not.toHaveBeenCalled();
  await r.unmount();
});
test("input 403 destroys every cached tab and pending download and notifies the shared authority", async () => {
  let reject!: (v: unknown) => void;
  let blob!: (v: Blob) => void;
  vi.spyOn(executionViewApi, "readContent")
    .mockResolvedValueOnce(response("cached output"))
    .mockReturnValueOnce(new Promise((_, r) => (reject = r)));
  vi.spyOn(executionViewApi, "downloadContent").mockReturnValue(new Promise((r) => (blob = r)));
  const create = vi.fn(() => "blob:test");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  const revoked = vi.fn();
  const r = await renderComponent(component({ onRevoked: revoked }));
  await click(r.container, "loadOutput");
  await click(r.container, "downloadOutput");
  await click(r.container, "loadInput");
  await act(async () => reject(new ApiError(403, "forbidden")));
  await act(async () => blob(new Blob(["late"])));
  expect(r.container.textContent).not.toContain("cached output");
  expect(r.container.textContent).not.toContain("Public summary");
  expect(r.container.textContent).toContain("unavailable");
  expect(create).not.toHaveBeenCalled();
  expect(revoked).toHaveBeenCalledTimes(1);
  await r.unmount();
});
test("download publishes only under current authority and revokes its object URL", async () => {
  vi.spyOn(executionViewApi, "downloadContent").mockResolvedValue(new Blob(["allowed"]));
  const create = vi.fn(() => "blob:test"),
    revoke = vi.fn(),
    save = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
  const r = await renderComponent(component());
  await click(r.container, "downloadOutput");
  expect(save).toHaveBeenCalledTimes(1);
  expect(create).toHaveBeenCalledTimes(1);
  await r.unmount();
  expect(revoke).toHaveBeenCalledWith("blob:test");
});
test("historical run-level artifact resolves a real fixed-version producer across pages", async () => {
  const producer = {
    ...step,
    step_id: "producer",
    artifact_refs: [{ artifact_id: "a", version: 2, availability: "available" as const }],
  };
  vi.spyOn(executionViewApi, "listSteps").mockResolvedValue({
    items: [producer],
    at: "cut",
    revision: 1,
    next_cursor: null,
    hidden_count: 0,
    completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
  });
  vi.spyOn(executionViewApi, "getStep").mockResolvedValue(producer as StepDetail);
  const read = vi.spyOn(executionViewApi, "readArtifact").mockResolvedValue(
    response("<script>secret()</script>", {
      content_type: "text/html",
      artifact_id: "a",
      version: 2,
    }),
  );
  const selected = parseSelection("run=r&at=cut&artifact=a&version=2&panel=artifact").selection;
  const r = await renderComponent(
    component({
      selection: selected,
      detail: null,
      page: { ...page, steps: [], next_cursor: "more" },
    }),
  );
  await click(r.container, "loadArtifact");
  expect(read).toHaveBeenCalledWith(
    "a",
    {
      version: 2,
      run_id: "r",
      step_id: "producer",
      at: "cut",
      presentation: true,
      limit_bytes: 65536,
      cursor: undefined,
    },
    expect.anything(),
  );
  expect(r.container.querySelector("script")).toBeNull();
  expect(r.container.querySelector("iframe")?.getAttribute("sandbox")).toBe("");
  expect(r.container.querySelector("iframe")?.srcdoc).not.toContain("secret()");
  await r.unmount();
});

test("an unavailable download continuation clears already loaded body rather than retaining it", async () => {
  vi.spyOn(executionViewApi, "readContent").mockResolvedValue(response("cached output"));
  vi.spyOn(executionViewApi, "downloadContent").mockRejectedValue(
    new ApiError(409, "resource_unavailable", { code: "resource_unavailable" }),
  );
  const revoked = vi.fn(),
    r = await renderComponent(component({ onRevoked: revoked }));
  await click(r.container, "loadOutput");
  await click(r.container, "downloadOutput");
  expect(r.container.textContent).not.toContain("cached output");
  expect(revoked).toHaveBeenCalledTimes(1);
  await r.unmount();
});
test("multiple historical producers require an explicit choice and never drop the authority triple", async () => {
  const producers = ["first", "second"].map((step_id) => ({
    ...step,
    step_id,
    artifact_refs: [{ artifact_id: "a", version: 2, availability: "available" as const }],
  }));
  vi.spyOn(executionViewApi, "getStep").mockImplementation(
    async (_run, id) => producers.find((p) => p.step_id === id)!,
  );
  const read = vi.spyOn(executionViewApi, "readArtifact").mockResolvedValue(response("content"));
  const r = await renderComponent(
    component({
      selection: parseSelection("run=r&at=cut&artifact=a&version=2&panel=artifact").selection,
      detail: null,
      page: { ...page, steps: producers },
    }),
  );
  expect(read).not.toHaveBeenCalled();
  expect(
    Array.from(r.container.querySelectorAll("button")).find(
      (b) => b.textContent === "loadArtifact",
    ),
  ).toBeUndefined();
  await act(async () => {
    const select = r.container.querySelector("select")!;
    select.value = "second";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await click(r.container, "loadArtifact");
  expect(read.mock.calls[0][1]).toMatchObject({
    run_id: "r",
    step_id: "second",
    at: "cut",
    version: 2,
  });
  await r.unmount();
});
test("a missing fixed-version historical producer is unavailable, never read as latest", async () => {
  const read = vi.spyOn(executionViewApi, "readArtifact");
  const r = await renderComponent(
    component({
      selection: parseSelection("run=r&at=cut&artifact=a&version=2&panel=artifact").selection,
      detail: null,
      page: { ...page, steps: [] },
    }),
  );
  expect(r.container.textContent).toContain("unavailableKind");
  expect(read).not.toHaveBeenCalled();
  await r.unmount();
});
test.each([
  { truncated: true, next_cursor: null },
  { truncated: false, next_cursor: "next" },
])("inconsistent truncation metadata is not rendered", async (extra) => {
  vi.spyOn(executionViewApi, "readContent").mockResolvedValue(response("invalid body", extra));
  const r = await renderComponent(component());
  await click(r.container, "loadOutput");
  expect(r.container.textContent).not.toContain("invalid body");
  expect(r.container.textContent).toContain("readError");
  await r.unmount();
});
test("canonical file source downloads use the fixed citation reader", async () => {
  const cited = {
    ...step,
    citation_refs: [
      { citation_id: "c", resource_kind: "file" as const, availability: "available" as const },
    ],
  };
  const download = vi
    .spyOn(executionViewApi, "downloadFileSource")
    .mockResolvedValue(new Blob(["fixed file"]));
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:test"),
  });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const r = await renderComponent(
    component({ detail: cited, selection: { ...selection, citationId: "c", panel: "source" } }),
  );
  await click(r.container, "downloadSource");
  expect(download).toHaveBeenCalledWith("c", expect.objectContaining({ workspaceId: "w" }));
  await r.unmount();
});

test("scope revision removes loaded input immediately", async () => {
  vi.spyOn(executionViewApi, "readContent").mockResolvedValue(response("old scope input"));
  const r = await renderComponent(component());
  await click(r.container, "loadInput");
  expect(r.container.textContent).toContain("old scope input");
  scope.revision++;
  await act(async () => r.root.render(component()));
  expect(r.container.textContent).not.toContain("old scope input");
  await r.unmount();
});
test("a repeated continuation cursor cannot append duplicated data", async () => {
  vi.spyOn(executionViewApi, "readContent")
    .mockResolvedValueOnce(response("first", { truncated: true, next_cursor: "same" }))
    .mockResolvedValueOnce(response("duplicated", { truncated: true, next_cursor: "same" }));
  const r = await renderComponent(component());
  await click(r.container, "loadOutput");
  await click(r.container, "nextPage");
  expect(r.container.textContent).not.toContain("duplicated");
  expect(r.container.textContent).toContain("readError");
  await r.unmount();
});
test("changing an explicit producer cancels the preceding producer download", async () => {
  const producers = ["first", "second"].map((step_id) => ({
    ...step,
    step_id,
    artifact_refs: [{ artifact_id: "a", version: 2, availability: "available" as const }],
  }));
  vi.spyOn(executionViewApi, "getStep").mockImplementation(
    async (_run, id) => producers.find((p) => p.step_id === id)!,
  );
  let resolve!: (value: Blob) => void;
  vi.spyOn(executionViewApi, "downloadArtifact").mockReturnValue(new Promise((r) => (resolve = r)));
  const create = vi.fn(() => "blob:test");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const r = await renderComponent(
    component({
      selection: parseSelection("run=r&at=cut&artifact=a&version=2&panel=artifact").selection,
      detail: null,
      page: { ...page, steps: producers },
    }),
  );
  async function choose(value: string) {
    await act(async () => {
      const select = r.container.querySelector("select")!;
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  await choose("first");
  await click(r.container, "downloadArtifact");
  await choose("second");
  await act(async () => resolve(new Blob(["old producer"])));
  expect(create).not.toHaveBeenCalled();
  await r.unmount();
});

test("a proven missing locator clears its body but preserves explicit same-revision fallback", async () => {
  const cited = {
    ...step,
    citation_refs: [
      {
        citation_id: "c",
        resource_kind: "knowledge_base" as const,
        knowledge_base_id: "kb",
        version_id: "v1",
        document_revision_id: "r1",
        doc_id: "d",
        chunk_id: "gone",
        page_no: 2,
        availability: "available" as const,
      },
    ],
  };
  const read = vi
    .spyOn(executionViewApi, "readSource")
    .mockResolvedValueOnce({
      availability: "unavailable",
      reason: "source_locator_unavailable",
      content_type: "text/plain",
      redacted: false,
      truncated: false,
    })
    .mockResolvedValueOnce(response("Same revision page"));
  const revoked = vi.fn();
  const r = await renderComponent(
    component({
      detail: cited,
      selection: { ...selection, citationId: "c", panel: "source" },
      onRevoked: revoked,
    }),
  );
  await click(r.container, "loadSource");
  expect(revoked).not.toHaveBeenCalled();
  await act(async () => {
    (r.container.querySelector('[data-source-fallback="page"]') as HTMLButtonElement).click();
  });
  await click(r.container, "loadSource");
  expect(read).toHaveBeenLastCalledWith(
    "c",
    expect.objectContaining({ locator: "page" }),
    expect.anything(),
  );
  expect(r.container.textContent).toContain("Same revision page");
  await r.unmount();
});

test("artifact-only canonical citation is resolved through same-cut provenance without current pin repair", async () => {
  const producer = {
    ...step,
    artifact_refs: [{ artifact_id: "a", version: 1, availability: "available" as const }],
    citation_refs: [],
  };
  vi.spyOn(executionViewApi, "getStep").mockResolvedValue(producer);
  vi.spyOn(executionViewApi, "getProvenance").mockResolvedValue([
    {
      artifact_id: "a",
      version: 1,
      producer_run_id: "r",
      producer_step_ids: ["step"],
      binding_status: "bound",
      evidence_kind: "direct",
      availability: "available",
      activity_id: null,
      attempt_id: null,
      invocation_id: null,
      produced_event_id: null,
      citation_refs: [
        {
          citation_id: "alias",
          availability: "available",
          resource_kind: "knowledge_base",
          version_id: "v-original",
          document_revision_id: "rev-original",
          doc_id: "d",
          knowledge_base_id: "kb",
        },
      ],
    },
  ]);
  const read = vi
    .spyOn(executionViewApi, "readSource")
    .mockResolvedValue(response("canonical source"));
  const r = await renderComponent(
    component({
      detail: null,
      page: { ...page, steps: [producer] },
      selection: parseSelection("run=r&at=cut&citation=alias&panel=source").selection,
    }),
  );
  await click(r.container, "loadSource");
  expect(read).toHaveBeenCalledWith(
    "alias",
    { cursor: undefined, limit_bytes: 65536 },
    expect.anything(),
  );
  expect(r.container.textContent).toContain("v-original");
  expect(r.container.textContent).toContain("canonical source");
  await r.unmount();
});
