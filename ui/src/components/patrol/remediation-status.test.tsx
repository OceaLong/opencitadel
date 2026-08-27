// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it } from "vitest";

import type { PatrolRemediation, PatrolRemediationStatus } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";
import { RemediationStatusList } from "./remediation-status";

afterEach(() => {
  document.body.replaceChildren();
});

const ALL_STATUSES: PatrolRemediationStatus[] = [
  "proposed",
  "executing",
  "executed",
  "verified",
  "failed",
  "cancelled",
];

function remediationFixture(overrides: Partial<PatrolRemediation> = {}): PatrolRemediation {
  return {
    id: overrides.id ?? "remediation-1",
    pack_id: "pack-1",
    run_id: "run-1",
    finding_id: "finding-1",
    check_result_id: "result-1",
    fingerprint: "f".repeat(64),
    session_id: "session-1",
    action: "restart_workload",
    target_namespace: "opencitadel",
    target_workload: "api",
    target_kind: "Deployment",
    params: {},
    params_hash: "hash",
    impact_summary: "Restart Deployment/api",
    rollback_hint: "n/a",
    idempotency_key: "idem-1",
    actuator_capability_hash: "cap-hash",
    status: "proposed",
    before_observation: null,
    after_observation: null,
    recheck_run_id: null,
    error_code: null,
    error_message: null,
    created_by: "user-1",
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
    ...overrides,
  };
}

async function renderList(
  remediations: PatrolRemediation[],
  locale: "en" | "zh",
  messages: typeof en,
) {
  return renderComponent(
    <NextIntlClientProvider locale={locale} messages={messages}>
      <RemediationStatusList remediations={remediations} />
    </NextIntlClientProvider>,
  );
}

describe.each([
  ["en", en],
  ["zh", zh],
] as const)("RemediationStatusList %s", (locale, messages) => {
  it("renders a badge for every remediation status in the six-state enum", async () => {
    const remediations = ALL_STATUSES.map((status, index) =>
      remediationFixture({ id: `remediation-${index}`, status, session_id: `session-${index}` }),
    );
    const { container, unmount } = await renderList(remediations, locale, messages);
    for (const status of ALL_STATUSES) {
      expect(container.textContent).toContain(messages.patrol.remediation.status[status]);
    }
    await unmount();
  });

  it("shows the recheck run link only when recheck_run_id is present", async () => {
    const remediations = [
      remediationFixture({ id: "no-recheck", status: "executing", recheck_run_id: null }),
      remediationFixture({
        id: "with-recheck",
        status: "verified",
        recheck_run_id: "run-recheck-1",
      }),
    ];
    const { container, unmount } = await renderList(remediations, locale, messages);
    const links = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toContain("/patrol-runs/run-recheck-1");
    expect(links.filter((href) => href?.startsWith("/patrol-runs/"))).toHaveLength(1);
    expect(container.textContent).toContain(messages.patrol.remediation.viewRecheckRun);
    await unmount();
  });

  it("shows the server-authoritative impact summary and rollback hint", async () => {
    const remediations = [
      remediationFixture({
        impact_summary: "Restart Deployment/api: causes a rolling pod recreation.",
        rollback_hint: "Restart is non-destructive; no rollback action required.",
      }),
    ];
    const { container, unmount } = await renderList(remediations, locale, messages);
    expect(container.textContent).toContain(messages.patrol.remediation.impactSummaryLabel);
    expect(container.textContent).toContain(
      "Restart Deployment/api: causes a rolling pod recreation.",
    );
    expect(container.textContent).toContain(messages.patrol.remediation.rollbackHintLabel);
    expect(container.textContent).toContain(
      "Restart is non-destructive; no rollback action required.",
    );
    await unmount();
  });

  it("shows the approval session link when session_id is present", async () => {
    const remediations = [remediationFixture({ session_id: "session-42" })];
    const { container, unmount } = await renderList(remediations, locale, messages);
    const links = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toContain("/sessions/session-42");
    await unmount();
  });

  it("renders an empty state with no crash when there are no remediations", async () => {
    const { container, unmount } = await renderList([], locale, messages);
    expect(container.textContent).toContain(messages.patrol.remediation.empty);
    for (const status of ALL_STATUSES) {
      expect(container.textContent).not.toContain(messages.patrol.remediation.status[status]);
    }
    await unmount();
  });
});
