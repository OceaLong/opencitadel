// @vitest-environment jsdom
import { act } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  dataset: vi.fn(),
  history: vi.fn(),
  update: vi.fn(),
  publish: vi.fn(),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/components/evaluation/resource-picker", () => ({ ResourcePicker: () => null }));
vi.mock("@/lib/api/evaluations", () => ({
  evaluationApi: {
    dataset: mocks.dataset,
    datasetVersions: mocks.history,
    updateCase: mocks.update,
    publishDataset: mocks.publish,
  },
}));
import { DatasetEditor } from "./dataset-editor";
beforeEach(() => vi.clearAllMocks());
const access = { workspaceId: "w", canManage: true, canRun: false, canRegister: false };
async function textInput(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      input instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype,
      "value",
    )!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
test("browsing reads only, saves expected revision, and publishes then refreshes draft", async () => {
  const draft = { id: "d", name: "Dataset", revision: 3, cases: [] };
  mocks.dataset.mockResolvedValue(draft);
  mocks.history.mockResolvedValue({ items: [], next_cursor: null });
  mocks.update.mockResolvedValue({
    ...draft,
    revision: 4,
    cases: [
      { id: "c", case_key: "case-a", revision: 4, input: "Question", input_status: "provided" },
    ],
  });
  mocks.publish.mockResolvedValue({ id: "v", dataset_id: "d", revision: 1, cases: [] });
  const view = await renderComponent(<DatasetEditor id="d" access={access} />);
  expect(mocks.update).not.toHaveBeenCalled();
  expect(mocks.publish).not.toHaveBeenCalled();
  expect(mocks.dataset.mock.calls[0][1]).toMatchObject({ workspaceId: "w" });
  const labels = Array.from(view.container.querySelectorAll("label"));
  const field = (label: string) =>
    document.getElementById(
      labels.find((l) => l.textContent === label)!.htmlFor,
    ) as HTMLInputElement;
  await textInput(field("caseKey"), "case-a");
  await textInput(field("input"), "Question");
  await act(async () => {
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(mocks.update).toHaveBeenCalledTimes(1);
  expect(mocks.update.mock.calls[0][2]).toMatchObject({
    expected_revision: 3,
    case: { input: "Question" },
  });
  expect(view.container.textContent).toContain("revision 4");
  mocks.dataset.mockResolvedValue({ ...draft, revision: 5 });
  await act(async () => {
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "publish")!
      .click();
  });
  expect(mocks.publish.mock.calls[0][1]).toMatchObject({ expected_revision: 4 });
  expect(view.container.textContent).toContain("revision 5");
  await view.unmount();
});

function caseDraft() {
  return {
    id: "d",
    name: "Dataset",
    revision: 3,
    cases: [
      {
        id: "a",
        case_key: "case-a",
        revision: 1,
        input: "Body A",
        rules: [{ id: "schema", kind: "json_schema", schema: { const: "A" } }],
      },
      {
        id: "b",
        case_key: "case-b",
        revision: 1,
        input: "Body B",
        rules: [{ id: "schema", kind: "json_schema", schema: { const: "B" } }],
      },
    ],
  };
}
function field(container: HTMLElement, name: string) {
  const label = Array.from(container.querySelectorAll("label")).find(
    (l) => l.textContent === name,
  )!;
  return document.getElementById(label.htmlFor) as HTMLInputElement;
}
test("pending case save blocks selection and reconciles revision before editing B", async () => {
  const draft = caseDraft();
  mocks.dataset.mockResolvedValue(draft);
  mocks.history.mockResolvedValue({ items: [] });
  let finish!: (value: unknown) => void;
  mocks.update.mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const view = await renderComponent(<DatasetEditor id="d" access={access} />);
  const edits = () =>
    Array.from(view.container.querySelectorAll("button")).filter((b) => b.textContent === "edit");
  await act(async () => edits()[0].click());
  await textInput(field(view.container, "input"), "Saved A");
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  await act(async () => edits()[1].click());
  expect(field(view.container, "caseKey").value).toBe("case-a");
  expect(edits()[1].disabled).toBe(true);
  expect(
    Array.from(view.container.querySelectorAll("select")).find(
      (s) => s.labels?.[0]?.textContent === "fixedVersion",
    )?.disabled,
  ).toBe(true);
  await act(async () =>
    finish({
      ...draft,
      revision: 4,
      cases: [{ ...draft.cases[0], input: "Saved A" }, draft.cases[1]],
    }),
  );
  await act(async () => edits()[1].click());
  expect(field(view.container, "caseKey").value).toBe("case-b");
  expect(field(view.container, "input").value).toBe("Body B");
  mocks.update.mockResolvedValueOnce({ ...draft, revision: 5 });
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.update.mock.calls[1].slice(0, 3)).toMatchObject([
    "d",
    "case-b",
    { expected_revision: 4, case: { input: "Body B" } },
  ]);
  await view.unmount();
});
test("same-kind JSON rule switches case identity even with an invalid local draft", async () => {
  mocks.dataset.mockResolvedValue(caseDraft());
  mocks.history.mockResolvedValue({ items: [] });
  const view = await renderComponent(<DatasetEditor id="d" access={access} />);
  const edits = Array.from(view.container.querySelectorAll("button")).filter(
    (b) => b.textContent === "edit",
  );
  await act(async () => edits[0].click());
  const schema = () =>
    Array.from(view.container.querySelectorAll("label"))
      .find((l) => l.textContent?.startsWith("schema"))!
      .querySelector("textarea")!;
  expect(JSON.parse(schema().value)).toEqual({ const: "A" });
  await textInput(schema(), "{invalid");
  await act(async () => edits[1].click());
  expect(JSON.parse(schema().value)).toEqual({ const: "B" });
  expect(schema().validity.valid).toBe(true);
  await view.unmount();
});
