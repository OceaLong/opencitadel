// @vitest-environment jsdom
import { act } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  datasets: vi.fn(),
  dataset: vi.fn(),
  steps: vi.fn(),
  preview: vi.fn(),
  save: vi.fn(),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/hooks/use-capabilities", () => ({ useCapabilities: () => ({ snapshot: {} }) }));
vi.mock("@/lib/api/capabilities", () => ({ hasExecutionGrant: () => true }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: { userId: "u", workspaceId: "w" }, scopeRevision: 1 }),
}));
vi.mock("@/lib/api/evaluations", () => ({
  evaluationApi: {
    datasets: mocks.datasets,
    dataset: mocks.dataset,
    previewFromRun: mocks.preview,
    fromRun: mocks.save,
  },
}));
vi.mock("@/lib/api/execution-view", () => ({ executionViewApi: { listSteps: mocks.steps } }));
import { CaptureCaseAction } from "./capture-case-dialog";
const draft = { id: "d", revision: 3, cases: [] };
function deferred() {
  let resolve!: (value: unknown) => void;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
const button = (name: string) =>
  Array.from(document.querySelectorAll("button")).find((b) => b.textContent === name);
const select = (name: string) =>
  Array.from(document.querySelectorAll("select")).find((s) =>
    s.labels?.[0]?.textContent?.startsWith(name),
  )!;
async function choose(name: string, value: string) {
  await act(async () => {
    const s = select(name);
    s.value = value;
    s.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
async function key(value: string) {
  await act(async () => {
    const input = document.querySelector("input")!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function open() {
  const view = await renderComponent(<CaptureCaseAction runId="run" at="cut" />);
  await act(async () => button("captureCase")!.click());
  return view;
}
beforeEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
  mocks.datasets.mockResolvedValue([{ id: "d", name: "Dataset" }]);
  mocks.dataset.mockResolvedValue(draft);
  mocks.steps.mockResolvedValue({
    items: ["a", "b"].map((step_id) => ({
      step_id,
      activity_id: step_id,
      status: "completed",
      kind: "model",
    })),
  });
});
test.each(["step", "key"])(
  "late preview cannot restore an uninspected %s selection",
  async (change) => {
    const response = deferred();
    mocks.preview.mockReturnValue(response.promise);
    const view = await open();
    await choose("dataset", "d");
    await choose("sourceStep", "a");
    await key("case-a");
    await act(async () => button("inspect")!.click());
    if (change === "step") await choose("sourceStep", "b");
    else await key("case-b");
    await act(async () => response.resolve({ id: "a", input: "PREVIEW A", source_at: "cut" }));
    expect(document.body.textContent).not.toContain("PREVIEW A");
    expect(button("saveCase")).toBeUndefined();
    expect(mocks.save).not.toHaveBeenCalled();
    mocks.preview.mockResolvedValue({ id: "b", input: "PREVIEW B", source_at: "cut" });
    await act(async () => button("inspect")!.click());
    const save = deferred();
    mocks.save.mockReturnValue(save.promise);
    await act(async () => button("saveCase")!.click());
    expect(mocks.save.mock.calls[0][1]).toMatchObject({
      expected_revision: 3,
      step_id: change === "step" ? "b" : "a",
      case_key: change === "key" ? "case-b" : "case-a",
    });
    expect(select("dataset").disabled).toBe(true);
    expect(select("sourceStep").disabled).toBe(true);
    await act(async () => save.resolve({ ...draft, revision: 4 }));
    expect(document.body.textContent).toContain("editAndConfirm");
    await view.unmount();
  },
);
test("clearing a dataset selection invalidates its pending read", async () => {
  const response = deferred();
  mocks.dataset.mockReturnValue(response.promise);
  const view = await open();
  await choose("dataset", "d");
  await choose("dataset", "");
  await act(async () => response.resolve(draft));
  expect(select("dataset").value).toBe("");
  await choose("sourceStep", "a");
  await key("case-a");
  expect(button("inspect")!.disabled).toBe(true);
  await view.unmount();
});

test.each(["step", "key"])(
  "pending dataset read survives attempted dependent %s changes",
  async (change) => {
    const response = deferred();
    mocks.dataset.mockReturnValue(response.promise);
    const view = await open();
    await choose("sourceStep", "a");
    await key("case-a");
    await choose("dataset", "d");
    expect(select("sourceStep").disabled).toBe(true);
    expect(document.querySelector("input")!.disabled).toBe(true);
    expect(select("dataset").disabled).toBe(false);
    if (change === "step") await choose("sourceStep", "b");
    else await key("case-b");
    await act(async () => response.resolve(draft));
    expect(select("dataset").value).toBe("d");
    expect(button("inspect")!.disabled).toBe(false);
    mocks.preview.mockResolvedValue({ id: "preview", input: "current dataset" });
    await act(async () => button("inspect")!.click());
    expect(mocks.preview).toHaveBeenCalledWith(
      "d",
      expect.objectContaining({ expected_revision: 3 }),
      expect.objectContaining({ workspaceId: "w" }),
    );
    await view.unmount();
  },
);
