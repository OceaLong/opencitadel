// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const api = vi.hoisted(() => ({
  draft: vi.fn(),
  list: vi.fn(),
  version: vi.fn(),
  update: vi.fn(),
  publish: vi.fn(),
  preflight: vi.fn(),
  start: vi.fn(),
  datasets: vi.fn(),
  datasetVersions: vi.fn(),
  recordings: vi.fn(),
  environments: vi.fn(),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/lib/api/evaluations", () => ({ evaluationApi: api }));
import { VersionEditor } from "./version-editor";
const access = { workspaceId: "workspace", canManage: true, canRun: true, canRegister: false };
const definition = {
  dataset_version: "dv",
  config_versions: ["cv"],
  rubric_version: "rv",
  mode: "recorded",
  recording_versions: [],
  settings: {
    repeat: 1,
    seed: 0,
    token_budget: 100000,
    case_timeout_seconds: 1800,
    batch_timeout_seconds: 86400,
    subject_concurrency: 5,
    judge_concurrency: 2,
    environment_concurrency: 2,
    max_results: 5000,
    money_budget: null,
  },
};
test("native suite draft saves and publishes fixed versions; only explicit confirmed start sends batch command", async () => {
  api.draft.mockResolvedValue({ id: "suite", name: "Suite", revision: 3, definition });
  api.list.mockImplementation(
    async (kind: string, _o: unknown, _versions: unknown, _cursor: unknown, id: string) => ({
      items: id
        ? [{ id: "sv", name: "Suite", revision: 1 }]
        : [{ id: kind === "configs" ? "cv" : "rv", name: "Published choice", revision: 1 }],
      next_cursor: null,
    }),
  );
  api.version.mockImplementation(async (kind: string) =>
    kind === "configs"
      ? { id: "cv", purpose: "evaluation_subject" }
      : { id: "sv", name: "Suite", revision: 1, ...definition },
  );
  api.datasets.mockResolvedValue([]);
  api.recordings.mockResolvedValue({ items: [], next_cursor: null });
  api.update.mockResolvedValue({ id: "suite", name: "Suite", revision: 4, definition });
  api.publish.mockResolvedValue({ id: "sv", name: "Suite", revision: 1, ...definition });
  api.preflight.mockResolvedValue({
    id: "pf",
    suite_version: "sv",
    revision: 8,
    allowed: true,
    errors: [],
    warnings: [],
    evidence: {},
    quantity: 1,
    physical_call_upper_bound: null,
    price_coverage: "unknown",
    environment_ready: true,
    token_budget: 100000,
  });
  api.start.mockResolvedValue({ id: "batch" });
  const view = await renderComponent(<VersionEditor kind="suites" id="suite" access={access} />);
  const button = (text: string) =>
    Array.from(view.container.querySelectorAll("button")).find((b) => b.textContent === text)!;
  expect(api.start).not.toHaveBeenCalled();
  expect(api.preflight).not.toHaveBeenCalled();
  await act(async () => {
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(api.update.mock.calls[0][2]).toMatchObject({ expected_revision: 3, definition });
  api.draft.mockResolvedValue({ id: "suite", name: "Suite", revision: 5, definition });
  await act(async () => button("publish").click());
  expect(api.publish.mock.calls[0][2]).toMatchObject({ expected_revision: 4 });
  expect(view.container.textContent).toContain("immutable");
  expect(api.start).not.toHaveBeenCalled();
  await act(async () => button("checkPreflight").click());
  expect(api.preflight.mock.calls[0][1]).toMatchObject({ workspaceId: "workspace" });
  expect(button("start").disabled).toBe(true);
  await act(async () => {
    Array.from(view.container.querySelectorAll("label"))
      .find((l) => l.textContent === "confirmStart")!
      .querySelector("input")!
      .click();
  });
  await act(async () => button("start").click());
  expect(api.start).toHaveBeenCalledTimes(1);
  expect(api.start.mock.calls[0][0]).toMatchObject({ suite_version: "sv", preflight_revision: 8 });
  expect(api.start.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  expect(view.container.textContent).toContain("batchAccepted");
  await view.unmount();
});
