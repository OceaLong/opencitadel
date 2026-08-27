// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

vi.mock("next-intl", () => mockNextIntl());

import type { GovernanceProfile } from "@/lib/api/compliance";

import { GovernanceProfileView } from "./governance-profile-view";

const profile: GovernanceProfile = {
  session: {
    id: "s1",
    title: "t",
    status: "failed",
    operator_scope: "owned",
    operator_domains: ["example.com"],
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T01:00:00Z",
  },
  chain: { verified: true, checked_runs: 1, checked_entries: 7 },
  runs: [
    {
      run_id: "run-1",
      family: "agent",
      status: "failed",
      created_at: "2026-08-04T00:00:00Z",
      updated_at: "2026-08-04T01:00:00Z",
      terminal_at: "2026-08-04T01:00:00Z",
    },
  ],
  approvals: [
    {
      approval_id: "approval-1",
      run_id: "run-1",
      approval_kind: "tool_effect",
      subject_activity_id: "activity-1",
      subject_label: "shell_exec",
      risk_summary: "external write",
      status: "rejected",
      decision: "rejected",
      decided_by_user_id: "u1",
      feedback: "risky",
      requested_at: "2026-08-04T00:29:00Z",
      decided_at: "2026-08-04T00:30:00Z",
    },
  ],
  activities: [
    {
      activity_id: "activity-1",
      run_id: "run-1",
      activity_type: "tool.call",
      status: "failed",
      attempt: 1,
      failure_code: "ACTIVITY_HANDLER_ERROR",
      created_at: "2026-08-04T00:31:00Z",
      terminal_at: "2026-08-04T00:32:00Z",
    },
  ],
};

describe("GovernanceProfileView", () => {
  afterEach(() => document.body.replaceChildren());

  it("renders formal runs, approvals, activities and verified chain counts", async () => {
    const { container, unmount } = await renderComponent(
      <GovernanceProfileView profile={profile} />,
    );

    const text = container.textContent ?? "";
    expect(text).toContain("run-1");
    expect(text).toContain("shell_exec");
    expect(text).toContain("tool.call");
    expect(text).toContain("ACTIVITY_HANDLER_ERROR");
    expect(text).toContain("example.com");
    await unmount();
  });

  it.each([
    ["completed", "success"],
    ["failed", "destructive"],
    ["cancelled", "secondary"],
    ["pending", "secondary"],
    ["running", "secondary"],
    ["waiting", "secondary"],
  ] as const)("colors session status %s as %s", async (status, expectedVariant) => {
    const { container, unmount } = await renderComponent(
      <GovernanceProfileView profile={{ ...profile, session: { ...profile.session, status } }} />,
    );
    const badge = container.querySelector('[data-testid="session-status-badge"]');
    expect(badge?.textContent).toBe(status);
    if (expectedVariant === "success") expect(badge?.className).toContain("bg-success");
    else if (expectedVariant === "destructive")
      expect(badge?.className).toContain("bg-destructive/15");
    else expect(badge?.className).toContain("bg-muted");
    await unmount();
  });

  it("renders empty states when all formal timelines are empty", async () => {
    const { container, unmount } = await renderComponent(
      <GovernanceProfileView profile={{ ...profile, runs: [], approvals: [], activities: [] }} />,
    );
    expect(container.textContent).toContain("noData");
    await unmount();
  });
});
