// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { analysisApi } from "@/lib/api/execution-analysis";
import { ApiError } from "@/lib/api/fetch";
import type { ExportCreate, ExportJob } from "@/lib/api/types/execution-analysis";

import { renderComponent } from "@/test-utils/render";

import { ExportPanel } from "./export-panel";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/lib/api/execution-analysis", () => ({
  analysisApi: { createExport: vi.fn(), getExport: vi.fn(), downloadExport: vi.fn() },
}));
const access = {
  ownerKey: "u",
  workspaceId: "",
  canManage: true,
  canRun: false,
  canRegister: false,
};
const source: ExportCreate = {
  source_kind: "comparison",
  comparison_id: "c",
  revision: 1,
  format: "csv",
  request_id: "pending",
};
const job = { id: "export-one", status: "ready", format: "csv" } as ExportJob;
const click = async (container: HTMLElement, text: string) => {
  await act(async () => {
    const b = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(text));
    expect(b).toBeTruthy();
    b!.click();
  });
};
afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});
test("ready export is removed when format or complete comparison source changes", async () => {
  vi.mocked(analysisApi.createExport).mockResolvedValue(job);
  const view = await renderComponent(
    <ExportPanel access={access} source={source} description="source" />,
  );
  await click(view.container, "exportRevision");
  expect(view.container.textContent).toContain("download");
  await act(async () =>
    view.root.render(
      <ExportPanel access={access} source={{ ...source, format: "json" }} description="source" />,
    ),
  );
  expect(view.container.textContent).not.toContain("download");
  await click(view.container, "exportRevision");
  await act(async () =>
    view.root.render(
      <ExportPanel
        access={access}
        source={{ ...source, format: "json", revision: 2 }}
        description="source"
      />,
    ),
  );
  expect(view.container.textContent).not.toContain("download");
  await view.unmount();
});
test("pending old-format request is aborted and cannot populate replacement owner", async () => {
  let resolve!: (value: ExportJob) => void;
  vi.mocked(analysisApi.createExport).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const view = await renderComponent(
    <ExportPanel access={access} source={source} description="source" />,
  );
  await click(view.container, "exportRevision");
  const signal = vi.mocked(analysisApi.createExport).mock.calls[0][1]?.signal;
  await act(async () =>
    view.root.render(
      <ExportPanel access={access} source={{ ...source, format: "json" }} description="source" />,
    ),
  );
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(job));
  expect(view.container.textContent).not.toContain("download");
  await view.unmount();
});
test("transport retry reuses unresolved intent but accepted success creates a fresh intent", async () => {
  vi.mocked(analysisApi.createExport)
    .mockRejectedValueOnce(new Error("network"))
    .mockResolvedValue(job);
  const view = await renderComponent(
    <ExportPanel access={access} source={source} description="source" />,
  );
  await click(view.container, "exportRevision");
  await click(view.container, "exportRevision");
  await click(view.container, "exportRevision");
  const ids = vi.mocked(analysisApi.createExport).mock.calls.map(([body]) => body.request_id);
  expect(ids[0]).toBe(ids[1]);
  expect(ids[2]).not.toBe(ids[1]);
  await view.unmount();
});
test("expired accepted export permits a new intent with unchanged source", async () => {
  vi.mocked(analysisApi.createExport).mockResolvedValue(job);
  vi.mocked(analysisApi.getExport).mockRejectedValue(new ApiError(410, "expired"));
  const view = await renderComponent(
    <ExportPanel access={access} source={source} description="source" />,
  );
  await click(view.container, "exportRevision");
  await click(view.container, "download");
  expect(view.container.textContent).toContain("exportExpired");
  await click(view.container, "exportRevision");
  const calls = vi.mocked(analysisApi.createExport).mock.calls;
  expect(calls[0][0].request_id).not.toBe(calls[1][0].request_id);
  await view.unmount();
});
test("expired unresolved receipt retires its ID for the next explicit capture", async () => {
  vi.mocked(analysisApi.createExport)
    .mockRejectedValueOnce(new Error("lost response"))
    .mockRejectedValueOnce(new ApiError(410, "export_expired"))
    .mockResolvedValue(job);
  const view = await renderComponent(
    <ExportPanel access={access} source={source} description="source" />,
  );
  await click(view.container, "exportRevision");
  await click(view.container, "exportRevision");
  await click(view.container, "exportRevision");
  const ids = vi.mocked(analysisApi.createExport).mock.calls.map(([body]) => body.request_id);
  expect(ids[0]).toBe(ids[1]);
  expect(ids[2]).not.toBe(ids[1]);
  await view.unmount();
});
test("download extension comes from the accepted job format", async () => {
  vi.mocked(analysisApi.createExport).mockResolvedValue({ ...job, format: "json" });
  vi.mocked(analysisApi.getExport).mockResolvedValue({ ...job, format: "json" });
  vi.mocked(analysisApi.downloadExport).mockResolvedValue(new Blob(["{}"]));
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  const downloads: string[] = [];
  const anchor = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    downloads.push(this.download);
  });
  const view = await renderComponent(
    <ExportPanel access={access} source={source} description="source" />,
  );
  await click(view.container, "exportRevision");
  await click(view.container, "download");
  expect(downloads).toEqual(["execution-export-export-one.json"]);
  anchor.mockRestore();
  await view.unmount();
});
