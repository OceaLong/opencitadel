// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({ getGovernanceOverview: vi.fn() }));

vi.mock("next-intl", () =>
  mockNextIntl({
    pageTitle: "Governance overview",
    pageDescription: "desc",
    noData: "No data",
    statPendingApprovals: "Pending approvals",
    statOutcomesHint: (values?: Record<string, unknown>) =>
      `${values?.approved ?? 0} approved / ${values?.rejected ?? 0} rejected`,
    statAvgDecisionTime: "Avg decision time",
    statAvgDecisionTimeHint: "hint",
    statInterceptions: "Interceptions",
    statInterceptionsHint: "hint",
    statChainStatus: "Audit chain",
    chainIntact: "Chain intact",
    chainBroken: "Chain broken",
    chainBrokenAt: (values?: Record<string, unknown>) => `Broken at seq ${values?.seq ?? 0}`,
    approvalOutcomesTitle: "Approval outcomes",
    approvalOutcomesDesc: "desc",
    outcomeApproved: "Approved",
    outcomeRejected: "Rejected",
    outcomeCancelled: "Cancelled",
    timeRange7d: "7d",
    timeRange30d: "30d",
    timeRange90d: "90d",
    timeRangeAll: "All",
  }),
);
vi.mock("@/lib/api/compliance", () => ({
  complianceApi: { getGovernanceOverview: mocks.getGovernanceOverview },
}));
vi.mock("@/components/admin/governance-overview-charts", () => ({
  InterceptionsChart: ({ data }: { data: unknown[] }) => (
    <div data-testid="interceptions-chart">{data.length}</div>
  ),
  PatrolTrendChart: ({ data }: { data: unknown[] }) => (
    <div data-testid="patrol-chart">{data.length}</div>
  ),
  RemediationStatusChart: ({ remediation }: { remediation: { success_rate: number | null } }) => (
    <div data-testid="remediation-chart">{String(remediation.success_rate)}</div>
  ),
}));

import AdminGovernancePage from "./page";

const SAMPLE_OVERVIEW = {
  approvals: {
    pending_count: 3,
    avg_decision_seconds: 45.2,
    outcomes: { approved: 5, rejected: 2, cancelled: 1 },
  },
  interceptions: [
    { date: "2026-08-01", approval_requests: 2, activity_failures: 1 },
    { date: "2026-08-02", approval_requests: 1, activity_failures: 0 },
  ],
  patrol: [{ date: "2026-08-01", runs: 2, findings: 1 }],
  remediation: {
    by_status: { proposed: 0, executing: 0, executed: 1, verified: 2, failed: 0, cancelled: 0 },
    success_rate: 2 / 3,
  },
  chain: { ok: true, total: 100, first_broken_seq: null, checked_at: "2026-08-13T00:00:00Z" },
};

async function renderPage() {
  return renderComponent(<AdminGovernancePage />);
}

describe("AdminGovernancePage", () => {
  afterEach(() => {
    mocks.getGovernanceOverview.mockReset();
    document.body.replaceChildren();
  });

  it("fetches the overview with the default 30-day window and renders stat cards + charts", async () => {
    mocks.getGovernanceOverview.mockResolvedValue(SAMPLE_OVERVIEW);
    const { container, unmount } = await renderPage();

    expect(mocks.getGovernanceOverview).toHaveBeenCalledWith({ days: 30 });
    expect(container.textContent).toContain("3"); // pending approvals
    expect(container.textContent).toContain("45s"); // avg decision time rounded
    expect(container.textContent).toContain("Chain intact");
    expect(container.querySelector("[data-testid='interceptions-chart']")?.textContent).toBe("2");
    expect(container.querySelector("[data-testid='patrol-chart']")?.textContent).toBe("1");
    await unmount();
  });

  it("shows a broken-chain badge and the first broken sequence when the chain is unhealthy", async () => {
    mocks.getGovernanceOverview.mockResolvedValue({
      ...SAMPLE_OVERVIEW,
      chain: { ok: false, total: 100, first_broken_seq: 42, checked_at: "2026-08-13T00:00:00Z" },
    });
    const { container, unmount } = await renderPage();

    expect(container.textContent).toContain("Chain broken");
    expect(container.textContent).toContain("Broken at seq 42");
    await unmount();
  });

  it("renders honest empty state when there is no approval decision data yet", async () => {
    mocks.getGovernanceOverview.mockResolvedValue({
      ...SAMPLE_OVERVIEW,
      approvals: {
        pending_count: 0,
        avg_decision_seconds: null,
        outcomes: { approved: 0, rejected: 0, cancelled: 0 },
      },
    });
    const { container, unmount } = await renderPage();

    expect(container.textContent).toContain("No data");
    await unmount();
  });
});
