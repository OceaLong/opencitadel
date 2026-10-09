// @vitest-environment jsdom
import { act, useState } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  tools: vi.fn(),
  models: vi.fn(),
  skills: vi.fn(),
  recordings: vi.fn(),
  result: vi.fn(),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/lib/api/evaluations", () => ({
  evaluationApi: {
    builtinTools: mocks.tools,
    recordings: mocks.recordings,
    recordingResult: mocks.result,
  },
}));
vi.mock("@/lib/api/inference", () => ({ inferenceApi: { listModels: mocks.models } }));
vi.mock("@/lib/api/skills", () => ({ skillsApi: { list: mocks.skills } }));
vi.mock("./resource-picker", () => ({ ResourcePicker: () => null }));
import { ConfigFields, emptyConfig } from "./config-fields";
test("no-skill configuration selects server-owned builtin tools with current scope and mode", async () => {
  mocks.models.mockResolvedValue({ items: [] });
  mocks.skills.mockResolvedValue({ skills: [] });
  mocks.tools.mockResolvedValue([{ name: "read_file", mode: "agent" }]);
  const change = vi.fn();
  const view = await renderComponent(
    <ConfigFields
      value={emptyConfig()}
      onChange={change}
      access={{ workspaceId: "w", canManage: true, canRun: false, canRegister: false }}
    />,
  );
  const checkbox = Array.from(view.container.querySelectorAll("label"))
    .find((l) => l.textContent === "read_file")
    ?.querySelector("input");
  expect(checkbox).toBeTruthy();
  expect(mocks.tools.mock.calls[0]).toMatchObject(["agent", undefined, { workspaceId: "w" }]);
  await act(async () => checkbox!.click());
  expect(change).toHaveBeenLastCalledWith({ ...emptyConfig(), tool_names: ["read_file"] });
  await view.unmount();
});

import { RecordingPicker } from "./execution-bindings";
const access = { workspaceId: "w", canManage: true, canRun: false, canRegister: false };
function recordings() {
  mocks.models.mockResolvedValue({ items: [] });
  mocks.skills.mockResolvedValue({ skills: [] });
  mocks.tools.mockResolvedValue([]);
  mocks.recordings.mockResolvedValue({
    items: [
      { id: "a", status: "ready" },
      { id: "b", status: "ready" },
    ],
  });
  mocks.result.mockImplementation(async (id: string) => ({
    version_id: id,
    revision: 1,
    slot_count: 1,
    tool_names: ["tool-" + id],
  }));
}
test("configuration switches recording A to B with only B's tools", async () => {
  recordings();
  const change = vi.fn();
  function Editor() {
    const [value, setValue] = useState({
      ...emptyConfig(),
      external_contract_ref: { kind: "recording" as const, version_id: "a" },
      tool_names: ["tool-a"],
    });
    return (
      <ConfigFields
        value={value}
        access={access}
        onChange={(next) => {
          change(next);
          setValue(next as typeof value);
        }}
      />
    );
  }
  const view = await renderComponent(<Editor />);
  const choice = Array.from(view.container.querySelectorAll("select")).find(
    (s) => s.labels?.[0]?.textContent === "recordings",
  )!;
  expect(choice.value).toBe("a");
  await act(async () => {
    choice.value = "b";
    choice.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(change).toHaveBeenLastCalledWith(
    expect.objectContaining({
      external_contract_ref: { kind: "recording", version_id: "b" },
      tool_names: ["tool-b"],
    }),
  );
  await view.unmount();
});
test("suite recording picker still combines distinct manifests and tools", async () => {
  recordings();
  const change = vi.fn();
  const view = await renderComponent(
    <RecordingPicker access={access} values={["a"]} onChange={change} />,
  );
  await act(async () =>
    Array.from(view.container.querySelectorAll("label"))
      .find((l) => l.textContent?.includes("tool-b"))!
      .querySelector("input")!
      .click(),
  );
  expect(change).toHaveBeenLastCalledWith(["a", "b"], ["tool-a", "tool-b"]);
  await view.unmount();
});
