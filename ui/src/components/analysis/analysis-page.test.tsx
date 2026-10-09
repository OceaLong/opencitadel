// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, test, vi } from "vitest";

import {
  navigationOwner,
  readNavigationContext,
  saveNavigationContext,
} from "@/lib/analysis-view/navigation-context";
import { emptySelection } from "@/lib/analysis-view/selection";
import { analysisApi } from "@/lib/api/execution-analysis";
import { ApiError } from "@/lib/api/fetch";
import type { AnalysisRunPage, AnalysisSummary } from "@/lib/api/types/execution-analysis";

import { renderComponent } from "@/test-utils/render";

import { AnalysisPage } from "./analysis-page";
const nav = vi.hoisted(() => ({ search: new URLSearchParams(), push: vi.fn(), replace: vi.fn() }));
vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));
vi.mock("next/navigation", () => ({ useRouter: () => nav, useSearchParams: () => nav.search }));
vi.mock("@/lib/api/execution-analysis", () => ({
  analysisApi: {
    summary: vi.fn(),
    runs: vi.fn(),
    getPreferences: vi.fn(),
    updatePreferences: vi.fn(),
  },
}));
vi.mock("./run-charts", () => ({ RunCharts: () => null }));
vi.mock("./metric-facts", () => ({ MetricFacts: () => null }));
vi.mock("./metric-summary", () => ({ MetricSummary: () => null }));
vi.mock("./evaluation-series", () => ({ RetainedEvaluationSeries: () => null }));
vi.mock("./export-panel", () => ({ ExportPanel: () => null }));
const summary = {
  watermark: "accepted-cut",
  timezone: "UTC",
  grain: "day",
  metric_version: "v1",
  metrics: {},
} as AnalysisSummary;
const page = (id: string, next_cursor: string | null = null): AnalysisRunPage => ({
  watermark: "accepted-cut",
  availability: "available",
  items: [{ run_id: id, status: "completed" }],
  next_cursor,
});
const access = {
  ownerKey: "scope",
  workspaceId: "team",
  canManage: true,
  canWritePreferences: true,
  canRun: false,
  canRegister: false,
  deny: vi.fn(),
};
const button = (c: HTMLElement, s: string) =>
  [...c.querySelectorAll("button")].find((b) => b.textContent === s)!;
function setup() {
  vi.mocked(analysisApi.summary).mockResolvedValue(summary);
  vi.mocked(analysisApi.runs).mockResolvedValue(page("run-first", "page-two"));
  vi.mocked(analysisApi.getPreferences).mockResolvedValue({ revision: 0, timezone: null });
}
afterEach(() => {
  vi.resetAllMocks();
  nav.search = new URLSearchParams();
  document.body.replaceChildren();
  sessionStorage.clear();
});
test("leaving a later page serializes accepted default query, capture, cursor and selection; Return restores that page", async () => {
  setup();
  const view = await renderComponent(<AnalysisPage access={access} />);
  await act(async () => button(view.container, "selectAllMatching").click());
  vi.mocked(analysisApi.runs).mockResolvedValue(page("run-second"));
  await act(async () => button(view.container, "next").click());
  await act(async () => button(view.container, "run-second").click());
  const target = new URL(nav.push.mock.calls[0][0], "http://localhost");
  const back = new URL(target.searchParams.get("analysis_return")!, "http://localhost");
  expect(target.toString().length).toBeLessThan(250);
  const restoredContext = readNavigationContext(
    "/analysis",
    back.searchParams,
    navigationOwner(access),
  )!;
  expect(restoredContext.params.get("watermark")).toBe("accepted-cut");
  expect(restoredContext.params.get("cursor")).toBe("page-two");
  expect(JSON.parse(restoredContext.params.get("filters")!).start).toBeTruthy();
  expect(back.hash).toBe("#run-run-second");
  await view.unmount();
  nav.search = back.searchParams;
  const restored = await renderComponent(<AnalysisPage access={access} />);
  expect(analysisApi.summary).toHaveBeenLastCalledWith(
    expect.objectContaining({ watermark: "accepted-cut" }),
    expect.anything(),
  );
  expect(analysisApi.runs).toHaveBeenLastCalledWith(
    expect.objectContaining({ watermark: "accepted-cut", cursor: "page-two" }),
    expect.anything(),
  );
  expect(restored.container.textContent).toContain("allMatching");
  await restored.unmount();
});
test("unavailable saved capture requires explicit new snapshot and clears old cursor", async () => {
  setup();
  nav.search = new URLSearchParams({ watermark: "old-cut", cursor: "old-page" });
  vi.mocked(analysisApi.summary).mockRejectedValueOnce(
    new ApiError(400, "analysis_refresh_required"),
  );
  const view = await renderComponent(<AnalysisPage access={access} />);
  expect(view.container.textContent).toContain("captureUnavailable");
  await act(async () => button(view.container, "newCapture").click());
  expect(vi.mocked(analysisApi.summary).mock.calls.at(-1)![0]!.watermark).toBeUndefined();
  expect(vi.mocked(analysisApi.runs).mock.calls.at(-1)![0].cursor).toBeUndefined();
  await view.unmount();
});
test("preference write 403 remains local while genuine analysis read 403 denies boundary", async () => {
  setup();
  vi.mocked(analysisApi.updatePreferences).mockRejectedValue(
    new ApiError(403, "analysis_preference_permission_denied"),
  );
  const view = await renderComponent(<AnalysisPage access={access} />);
  await act(async () => button(view.container, "saveTimezone").click());
  expect(access.deny).not.toHaveBeenCalled();
  expect(view.container.textContent).toContain("run-first");
  expect(view.container.textContent).toContain("denied");
  vi.mocked(analysisApi.runs).mockRejectedValue(
    new ApiError(403, "analysis_authorization_revoked"),
  );
  await act(async () => button(view.container, "next").click());
  expect(access.deny).toHaveBeenCalledTimes(1);
  await view.unmount();
});
test("ordinary member cannot save timezone while retaining authorized analysis", async () => {
  setup();
  const view = await renderComponent(
    <AnalysisPage access={{ ...access, canWritePreferences: false }} />,
  );
  expect(button(view.container, "saveTimezone").disabled).toBe(true);
  expect(view.container.textContent).toContain("run-first");
  await view.unmount();
});

test("transport failure while returning retries the same capture instead of declaring expiry", async () => {
  setup();
  nav.search = new URLSearchParams({ watermark: "accepted-cut", cursor: "page-two" });
  vi.mocked(analysisApi.summary).mockRejectedValueOnce(new Error("network unavailable"));
  const view = await renderComponent(<AnalysisPage access={access} />);
  expect(view.container.textContent).not.toContain("captureUnavailable");
  await act(async () => button(view.container, "refresh").click());
  expect(analysisApi.summary).toHaveBeenLastCalledWith(
    expect.objectContaining({ watermark: "accepted-cut" }),
    expect.anything(),
  );
  expect(analysisApi.runs).toHaveBeenLastCalledWith(
    expect.objectContaining({ cursor: "page-two" }),
    expect.anything(),
  );
  await view.unmount();
});

test.each(["missing", "foreign"])(
  "%s return context blocks reads until explicit new-analysis recovery",
  async (kind) => {
    setup();
    const params = new URLSearchParams({ watermark: "accepted-cut", cursor: "page-two" });
    const href = saveNavigationContext(
      {
        path: "/analysis",
        params,
        selection: { ...emptySelection(), runIds: ["private-selection"] },
      },
      kind === "foreign" ? "other-owner" : navigationOwner(access),
    );
    nav.search = new URL(href, "http://localhost").searchParams;
    if (kind === "missing") sessionStorage.clear();
    const view = await renderComponent(<AnalysisPage access={access} />);
    expect(analysisApi.summary).not.toHaveBeenCalled();
    expect(view.container.textContent).toContain("returnContextUnavailable");
    await act(async () => button(view.container, "startNewAnalysis").click());
    expect(vi.mocked(analysisApi.summary).mock.calls[0][0]?.watermark).toBeUndefined();
    expect(view.container.textContent).not.toContain("private-selection");
    await view.unmount();
  },
);
test("navigation storage quota keeps current selection and page, then explicit retry succeeds", async () => {
  setup();
  const view = await renderComponent(<AnalysisPage access={access} />);
  await act(async () => button(view.container, "selectAllMatching").click());
  const storage = window.sessionStorage;
  const mock = vi.spyOn(window, "sessionStorage", "get").mockReturnValue({
    length: 0,
    key: () => null,
    getItem: () => null,
    removeItem: () => {},
    clear: () => {},
    setItem: () => {
      throw new DOMException("quota", "QuotaExceededError");
    },
  });
  nav.push.mockClear();
  await act(async () => button(view.container, "run-first").click());
  expect(nav.push).not.toHaveBeenCalled();
  expect(view.container.textContent).toContain("returnContextSaveFailed");
  expect(view.container.textContent).toContain("allMatching");
  vi.mocked(analysisApi.runs).mockResolvedValue(page("run-second"));
  await act(async () => button(view.container, "next").click());
  expect(view.container.textContent).toContain("run-first");
  expect(view.container.textContent).not.toContain("run-second");
  mock.mockRestore();
  expect(window.sessionStorage).toBe(storage);
  await act(async () => button(view.container, "retrySaveContext").click());
  expect(view.container.textContent).toContain("run-second");
  expect(view.container.textContent).toContain("allMatching");
  expect(view.container.textContent).not.toContain("returnContextSaveFailed");
  await view.unmount();
});

test("bounded context retains comparison refresh intent after restoring and applying controls", async () => {
  setup();
  const comparisonId = "00000000-0000-4000-8000-000000000001";
  const params = new URLSearchParams({
    watermark: "accepted-cut",
    refresh_comparison: comparisonId,
    expected_revision: "3",
  });
  const href = saveNavigationContext(
    { path: "/analysis", params, selection: { ...emptySelection(), runIds: ["r"] } },
    navigationOwner(access),
  );
  nav.search = new URL(href, "http://localhost").searchParams;
  nav.replace.mockImplementation((href: string) => {
    nav.search = new URL(href, "http://localhost").searchParams;
  });
  const view = await renderComponent(<AnalysisPage access={access} />);
  expect(view.container.textContent).toContain("publishRefresh");
  await act(async () => view.container.querySelector("form")!.requestSubmit());
  expect(view.container.textContent).toContain("publishRefresh");
  const context = readNavigationContext("/analysis", nav.search, navigationOwner(access))!;
  expect(context.params.get("refresh_comparison")).toBe(comparisonId);
  expect(context.params.get("expected_revision")).toBe("3");
  await view.unmount();
});

test("render identity follows accepted capture and is revoked while another request is pending", async () => {
  setup();
  const view = await renderComponent(<AnalysisPage access={access} />);
  const content = view.container.querySelector('[data-native-view="analysis"]');
  expect(content?.getAttribute("data-native-ready")).toBe("true");
  expect(content?.getAttribute("data-public-watermark")).toBe("accepted-cut");
  expect(content?.getAttribute("data-public-metric-version")).toBe("v1");
  vi.mocked(analysisApi.summary).mockReturnValue(new Promise(() => {}));
  await act(async () => view.container.querySelector("form")!.requestSubmit());
  expect(
    view.container.querySelector('[data-native-view="analysis"][data-native-ready="true"]'),
  ).toBeNull();
  await view.unmount();
});
