// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { PatrolFinding, PatrolPack, PatrolRunDetail } from "@/lib/api/types";

import { mockNavigation, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  getPack: vi.fn(),
  proposeRemediation: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => mockNavigation({ push: mocks.push }));
vi.mock("sonner", () => mockSonner());
vi.mock("@/lib/api/patrols", () => ({
  patrolsApi: { getPack: mocks.getPack, proposeRemediation: mocks.proposeRemediation },
}));

import { RemediationDialog } from "./remediation-dialog";

// jsdom has no PointerEvent-driven pointer capture; Radix Select/Checkbox
// fall back to their plain-`click()` path (pointerTypeRef defaults to
// "touch", see @radix-ui/react-select's SelectTrigger/SelectItemImpl) as
// long as these three DOM APIs exist so the popper/viewport effects don't
// throw when the content mounts. `focus()` is additionally stubbed inert:
// Radix Dialog's FocusScope and Select's autofocus-selected-item behavior
// otherwise volley real jsdom focus/blur events back and forth forever
// (confirmed via a minimal Dialog+Select repro) once Select's content
// portals in -- a documented jsdom/nested-Radix-primitive interaction, not
// something specific to this dialog. Assertions below only touch DOM
// state/text, never actual focus, so this doesn't weaken what's verified.
beforeAll(() => {
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
  Element.prototype.hasPointerCapture = Element.prototype.hasPointerCapture ?? (() => false);
  Element.prototype.releasePointerCapture = Element.prototype.releasePointerCapture ?? (() => {});
  HTMLElement.prototype.focus = function () {};
});

function packFixture(overrides: {
  checkId?: string;
  probeTool?: string;
  probeArgs?: Record<string, unknown>;
} = {}): PatrolPack {
  const checkId = overrides.checkId ?? "check-1";
  return {
    id: "pack-1",
    owner_user_id: "user-1",
    name: "Daily",
    slug: "daily",
    status: "active",
    version: 1,
    config: {
      schema_version: 1,
      target_ref: "cluster-1",
      timezone: "UTC",
      schedule: { cron: "0 * * * *", enabled: true },
      scope: { cluster: "cluster-1", namespaces: ["opencitadel"], environment: "production" },
      defaults: { timeout_seconds: 30, run_timeout_seconds: 300, max_evidence_items: 20 },
      checks: [
        {
          id: checkId,
          title: "Workload availability",
          enabled: true,
          probe: {
            tool: overrides.probeTool ?? "k8s_workload_summary",
            args: overrides.probeArgs ?? { namespace: "opencitadel", workload: "api", kind: "Deployment" },
            output_schema_hash: "hash",
          },
          assertions: [],
          severity_on_fail: "critical",
          required_evidence: [],
        },
      ],
    },
    mcp_server_id: "server-1",
    skill_id: "skill-1",
    validation_summary: {},
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };
}

function findingFixture(overrides: Partial<PatrolFinding> = {}): PatrolFinding {
  return {
    id: "finding-1",
    run_id: "run-1",
    check_result_id: "result-1",
    fingerprint: "f".repeat(64),
    severity: "critical",
    status: "open",
    title: "k8s workload unavailable",
    summary: "unavailable replicas",
    first_seen_at: "2026-08-04T00:00:00Z",
    last_seen_at: "2026-08-04T00:00:00Z",
    occurrence_count: 1,
    ...overrides,
  };
}

function runFixture(checkId = "check-1"): PatrolRunDetail {
  return {
    id: "run-1",
    pack_id: "pack-1",
    pack_version: 1,
    status: "completed_with_findings",
    trigger_type: "manual",
    counts: { pass: 0, warn: 0, fail: 1, error: 0, skipped: 0 },
    summary: {},
    created_at: "2026-08-04T00:00:00Z",
    check_results: [
      {
        id: "result-1",
        run_id: "run-1",
        check_id: checkId,
        status: "fail",
        severity: "critical",
        observed: {},
        assertion_results: [],
        evidence_refs: [],
        explanation: "",
        fingerprint: "f".repeat(64),
        started_at: "2026-08-04T00:00:00Z",
        finished_at: "2026-08-04T00:00:00Z",
      },
    ],
    findings: [],
  };
}

async function renderDialog(finding: PatrolFinding, run: PatrolRunDetail) {
  const onOpenChange = vi.fn();
  const result = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <RemediationDialog open onOpenChange={onOpenChange} finding={finding} run={run} />
    </NextIntlClientProvider>,
  );
  // The check-loading effect resolves `patrolsApi.getPack` asynchronously;
  // flush it before interacting.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return { ...result, onOpenChange };
}

async function selectAction(actionLabel: string) {
  const trigger = document.querySelector('#remediation-action') as HTMLElement;
  await act(async () => {
    trigger.click();
    await Promise.resolve();
  });
  const item = Array.from(document.querySelectorAll('[role="option"]')).find(
    (el) => el.textContent === actionLabel,
  ) as HTMLElement | undefined;
  await act(async () => {
    item?.click();
    await Promise.resolve();
  });
}

function submitButton() {
  return Array.from(document.querySelectorAll("button")).find(
    (button) => button.textContent?.includes(en.patrol.remediation.dialog.submit),
  ) as HTMLButtonElement | undefined;
}

function impactCheckbox() {
  return document.querySelector("#remediation-impact-confirm") as HTMLButtonElement | null;
}

function workloadInput() {
  return document.querySelector("#remediation-workload") as HTMLInputElement | null;
}

afterEach(() => {
  mocks.getPack.mockReset();
  mocks.proposeRemediation.mockReset();
  mocks.push.mockReset();
  document.body.replaceChildren();
});

describe("RemediationDialog — backend allowed_actions", () => {
  it("uses the server-provided allowed_actions instead of recomputing from the probe tool", async () => {
    // The probe tool is non-k8s (the local mirror would say "no actions"),
    // but the Finding response says an action is allowed -- the dialog must
    // trust the server value.
    mocks.getPack.mockResolvedValue(packFixture({ probeTool: "http_probe" }));
    const finding = findingFixture({ allowed_actions: ["restart_workload"] });
    const { unmount } = await renderDialog(finding, runFixture());

    expect(document.body.textContent).not.toContain(
      en.patrol.remediation.dialog.noActionsAvailable,
    );
    const trigger = document.querySelector("#remediation-action");
    expect(trigger?.getAttribute("aria-disabled")).not.toBe("true");
    expect((trigger as HTMLButtonElement).disabled).toBe(false);
    await unmount();
  });

  it("falls back to the local probe-tool mirror when allowed_actions is absent", async () => {
    mocks.getPack.mockResolvedValue(packFixture({ probeTool: "k8s_workload_summary" }));
    const finding = findingFixture({ allowed_actions: undefined });
    const { unmount } = await renderDialog(finding, runFixture());

    await selectAction(en.patrol.remediation.action.scale_workload);
    expect(document.body.textContent).toContain(en.patrol.remediation.dialog.replicasLabel);
    await unmount();
  });

  it("treats a server-provided empty allowed_actions as authoritative (no fallback)", async () => {
    // A k8s_* probe tool would make the local mirror say "3 actions
    // allowed", but the server explicitly says none -- server wins.
    mocks.getPack.mockResolvedValue(packFixture({ probeTool: "k8s_workload_summary" }));
    const finding = findingFixture({ allowed_actions: [] });
    const { unmount } = await renderDialog(finding, runFixture());

    expect(document.body.textContent).toContain(en.patrol.remediation.dialog.noActionsAvailable);
    await unmount();
  });
});

describe("RemediationDialog — workload required gate", () => {
  it("blocks submit until a workload override is entered when the probe has no workload", async () => {
    mocks.getPack.mockResolvedValue(
      packFixture({ probeArgs: { namespace: "opencitadel", kind: "Deployment" } }),
    );
    const finding = findingFixture({ allowed_actions: ["restart_workload"] });
    const { unmount } = await renderDialog(finding, runFixture());

    expect(document.body.textContent).toContain(
      en.patrol.remediation.dialog.workloadRequiredHint,
    );

    await selectAction(en.patrol.remediation.action.restart_workload);
    const checkbox = impactCheckbox();
    await act(async () => {
      checkbox?.click();
    });

    expect(submitButton()?.disabled).toBe(true);

    const input = workloadInput();
    await act(async () => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          "value",
        )?.set;
        setter?.call(input, "api-deployment");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });

    expect(submitButton()?.disabled).toBe(false);
    await unmount();
  });

  it("does not require a workload override when the probe already detected one", async () => {
    mocks.getPack.mockResolvedValue(packFixture()); // default args include workload: "api"
    const finding = findingFixture({ allowed_actions: ["restart_workload"] });
    const { unmount } = await renderDialog(finding, runFixture());

    expect(document.body.textContent).toContain(
      en.patrol.remediation.dialog.workloadDetectedHint.replace("{workload}", "api"),
    );

    await selectAction(en.patrol.remediation.action.restart_workload);
    await act(async () => {
      impactCheckbox()?.click();
    });

    expect(submitButton()?.disabled).toBe(false);
    await unmount();
  });
});

describe("RemediationDialog — scale/rollback branches", () => {
  it("shows a replicas input for scale_workload and requires a positive integer", async () => {
    mocks.getPack.mockResolvedValue(packFixture());
    const finding = findingFixture({
      allowed_actions: ["restart_workload", "scale_workload", "rollback_workload"],
    });
    const { unmount } = await renderDialog(finding, runFixture());

    await selectAction(en.patrol.remediation.action.scale_workload);
    await act(async () => {
      impactCheckbox()?.click();
    });

    const replicasInput = document.querySelector("#remediation-replicas") as HTMLInputElement;
    expect(replicasInput).toBeTruthy();
    // Default "1" is a valid positive integer -> submit already enabled.
    expect(submitButton()?.disabled).toBe(false);

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(replicasInput, "0");
      replicasInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(submitButton()?.disabled).toBe(true);

    await unmount();
  });

  it("shows the rollback-to-previous hint and no replicas input for rollback_workload", async () => {
    mocks.getPack.mockResolvedValue(packFixture());
    const finding = findingFixture({
      allowed_actions: ["restart_workload", "scale_workload", "rollback_workload"],
    });
    const { unmount } = await renderDialog(finding, runFixture());

    await selectAction(en.patrol.remediation.action.rollback_workload);

    expect(document.body.textContent).toContain(
      en.patrol.remediation.dialog.rollbackToPreviousHint,
    );
    expect(document.querySelector("#remediation-replicas")).toBeNull();
    await unmount();
  });
});

describe("RemediationDialog — canSubmit", () => {
  it("stays disabled until the impact checkbox is confirmed, then submits", async () => {
    mocks.getPack.mockResolvedValue(packFixture());
    mocks.proposeRemediation.mockResolvedValue({ id: "rem-1", session_id: "session-1" });
    const finding = findingFixture({ allowed_actions: ["restart_workload"] });
    const { unmount } = await renderDialog(finding, runFixture());

    await selectAction(en.patrol.remediation.action.restart_workload);
    expect(submitButton()?.disabled).toBe(true);

    await act(async () => {
      impactCheckbox()?.click();
    });
    expect(submitButton()?.disabled).toBe(false);

    await act(async () => {
      submitButton()?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.proposeRemediation).toHaveBeenCalledWith("finding-1", {
      action: "restart_workload",
      params: {},
      workload: undefined,
    });
    expect(mocks.push).toHaveBeenCalledWith("/sessions/session-1");
    await unmount();
  });

  it("is disabled while the check is still loading, even with an action pre-selectable", async () => {
    let resolveGetPack: (pack: PatrolPack) => void = () => {};
    mocks.getPack.mockReturnValue(
      new Promise<PatrolPack>((resolve) => {
        resolveGetPack = resolve;
      }),
    );
    const finding = findingFixture({ allowed_actions: ["restart_workload"] });
    const onOpenChange = vi.fn();
    const { unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={en}>
        <RemediationDialog open onOpenChange={onOpenChange} finding={finding} run={runFixture()} />
      </NextIntlClientProvider>,
    );

    expect(submitButton()?.disabled).toBe(true);

    await act(async () => {
      resolveGetPack(packFixture());
      await Promise.resolve();
      await Promise.resolve();
    });
    await unmount();
  });
});
