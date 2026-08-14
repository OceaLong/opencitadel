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
    gate_profile: "strict",
    operator_scope: "owned",
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T01:00:00Z",
  },
  chain: { verified: true, checked_entries: 3 },
  approvals: [
    {
      action: "agent_tool_reject",
      decision: "reject",
      actor_user_id: "u1",
      created_at: "2026-08-04T00:30:00Z",
      pending_phase: "TOOL_APPROVAL_PHASE",
      tool: "shell_exec",
      approval_batch_id: "b1",
      feedback: "risky",
    },
  ],
  gate_hits: [
    {
      tool: "shell_exec",
      gated: true,
      gate_profile: "strict",
      created_at: "2026-08-04T00:29:00Z",
    },
  ],
  checkpoints: [
    { id: "c1", anchor_type: "step", label: "before-shell", created_at: "2026-08-04T00:28:00Z" },
  ],
  terminal: { status: "failed", reached_at: "2026-08-04T01:00:00Z" },
  denials: [
    {
      tool: "write_file",
      layer: "execution",
      reason: "当前会话策略禁止工具[write_file]",
      created_at: "2026-08-04T00:27:00Z",
    },
  ],
};

describe("GovernanceProfileView", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("renders approvals, gate hits, checkpoints and terminal state", async () => {
    const { container, unmount } = await renderComponent(
      <GovernanceProfileView profile={profile} />,
    );

    const text = container.textContent ?? "";
    expect(text).toContain("shell_exec");
    expect(text.toLowerCase()).toContain("reject");
    expect(text).toContain("before-shell");
    expect(text.toLowerCase()).toContain("failed");
    await unmount();
  });

  it("renders policy denials with tool, layer and reason", async () => {
    const { container, unmount } = await renderComponent(
      <GovernanceProfileView profile={profile} />,
    );

    const text = container.textContent ?? "";
    expect(text).toContain("write_file");
    expect(text).toContain("execution");
    expect(text).toContain("当前会话策略禁止工具[write_file]");
    await unmount();
  });

  it.each([
    ["completed", "success"],
    ["failed", "destructive"],
    ["cancelled", "secondary"],
    ["pending", "secondary"],
    ["running", "secondary"],
    ["waiting", "secondary"],
  ] as const)(
    "colors the terminal status badge %s as %s",
    async (status, expectedVariant) => {
      const statusProfile: GovernanceProfile = {
        ...profile,
        terminal: { status, reached_at: "2026-08-04T01:00:00Z" },
      };
      const { container, unmount } = await renderComponent(
        <GovernanceProfileView profile={statusProfile} />,
      );

      const badge = container.querySelector('[data-testid="terminal-status-badge"]');
      expect(badge?.textContent).toBe(status);
      if (expectedVariant === "success") {
        expect(badge?.getAttribute("data-variant")).toBeNull();
        expect(badge?.className).toContain("bg-success");
      } else {
        expect(badge?.getAttribute("data-variant")).toBe(expectedVariant);
      }
      await unmount();
    },
  );

  it("renders empty states when profile sections have no data", async () => {
    const emptyProfile: GovernanceProfile = {
      ...profile,
      approvals: [],
      gate_hits: [],
      checkpoints: [],
      denials: [],
    };
    const { container, unmount } = await renderComponent(
      <GovernanceProfileView profile={emptyProfile} />,
    );

    const text = container.textContent ?? "";
    expect(text).toContain("noData");
    await unmount();
  });
});
