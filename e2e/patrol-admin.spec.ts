import type { Locator, Page } from "@playwright/test";

import type { components } from "../ui/src/lib/api/generated/schema";
import type {
  ComplianceReport,
  EvidenceSessionItem,
  GovernanceOverview,
  GovernanceProfile,
} from "../ui/src/lib/api/compliance";
import type {
  PatrolPack,
  PatrolPackList,
  PatrolRunDetail,
} from "../ui/src/lib/api/types/patrols";
import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import {
  completeCleanupAction,
  registerCleanupAction,
} from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";
import { pollProjection } from "./support/poll";

type ActiveOperationsPolicy =
  components["schemas"]["ActiveOperationsPolicyResponse"];
type OperationsPolicy = components["schemas"]["OperationsPolicy-Input"];
type McpServer = components["schemas"]["MCPServerResponse"];

type User = { id: string; email: string; global_role: string };
type Team = { id: string; name: string; description: string };
type AdminOverview = {
  total_users: number;
  total_teams: number;
  total_sessions: number;
};
type AuditLog = {
  id: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  team_id: string | null;
  session_id: string | null;
};
type AuditList = { logs: AuditLog[]; total: number };

let patrolPackId = "";
let patrolRunId = "";
let patrolSessionId = "";
let patrolTeamId = "";
let patrolTeamName = "";
let patrolPackName = "";
let operatorUserId = "";

function cover(...requirementIds: string[]): void {
  for (const requirementId of requirementIds) {
    test
      .info()
      .annotations.push({ type: "acceptance", description: requirementId });
  }
}

async function setWorkspace(page: Page, teamId?: string): Promise<void> {
  if (!operatorUserId) throw new Error("operator identity must be loaded first");
  await page.evaluate(({ id, userId }) => {
    const value = id ?? "";
    window.localStorage.setItem("opencitadel-active-workspace", value);
    window.localStorage.setItem(
      `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
      value,
    );
  }, { id: teamId, userId: operatorUserId });
}

async function activateOperationsPolicy(
  page: Page,
  update: (policy: OperationsPolicy) => OperationsPolicy,
  note: string,
): Promise<ActiveOperationsPolicy> {
  const active = await appApi<ActiveOperationsPolicy>(
    page,
    "/runtime-policies/operations",
  );
  return (
    await appApi<ActiveOperationsPolicy>(
      page,
      "/runtime-policies/operations/revisions",
      {
        method: "POST",
        body: {
          expected_head_version: active.data.head.version,
          expected_active_revision_id: active.data.revision.id,
          policy: update(active.data.revision.policy),
          note,
        },
      },
    )
  ).data;
}

async function restoreOperationsPolicy(
  page: Page,
  revisionId: string,
): Promise<void> {
  const active = await appApi<ActiveOperationsPolicy>(
    page,
    "/runtime-policies/operations",
  );
  if (active.data.revision.id === revisionId) return;
  await appApi(
    page,
    `/runtime-policies/operations/revisions/${encodeURIComponent(revisionId)}/restore`,
    {
      method: "POST",
      body: {
        expected_head_version: active.data.head.version,
        expected_active_revision_id: active.data.revision.id,
        note: "Acceptance: restore original Operations Policy",
      },
    },
  );
}

async function assertNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  await expect
    .poll(
      () =>
        page.evaluate(() => ({
          client: document.documentElement.clientWidth,
          scroll: document.documentElement.scrollWidth,
        })),
      { message: `${label} must fit the mobile viewport` },
    )
    .toEqual({ client: 390, scroll: 390 });
}

async function tabToVisibleFocus(
  page: Page,
  target: Locator,
  label: string,
): Promise<void> {
  await expect(target, `${label} must be visible`).toBeVisible();
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  });
  let reached = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((node) => node === document.activeElement)) {
      reached = true;
      break;
    }
  }
  expect(reached, `${label} must be reachable with Tab`).toBe(true);
  const focus = await target.evaluate((node) => {
    const element = node as HTMLElement;
    const style = window.getComputedStyle(element);
    const accessibleName =
      element.getAttribute("aria-label") ??
      element.getAttribute("title") ??
      element.innerText;
    return {
      accessibleName: accessibleName.trim(),
      visibleIndicator:
        (style.outlineStyle !== "none" && style.outlineWidth !== "0px") ||
        style.boxShadow !== "none",
    };
  });
  expect(focus.accessibleName, `${label} needs an accessible name`).not.toBe("");
  expect(focus.visibleIndicator, `${label} needs a visible focus indicator`).toBe(
    true,
  );
}

test.describe.configure({ mode: "serial" });

test("Patrol Pack executes through the formal Collector path and fails closed when paused", async ({
  operatorPage: page,
}) => {
  cover("PAT-PACK", "PAT-RUN", "PAT-EVIDENCE", "PAT-ADMISSION");
  test.setTimeout(10 * 60_000);

  const currentUser = await appApi<User>(page, "/auth/me");
  operatorUserId = currentUser.data.id;
  patrolTeamName = acceptanceId("patrol-team");
  patrolPackName = acceptanceId("patrol-pack");
  const team = (
    await appApi<Team>(page, "/teams", {
      method: "POST",
      body: {
        name: patrolTeamName,
        description: "Acceptance-owned Patrol workspace",
      },
    })
  ).data;
  patrolTeamId = team.id;
  registerCleanupAction({
    action: "delete-resource",
    resource: "team",
    resource_id: team.id,
  });
  await setWorkspace(page, team.id);

  const originalPolicy = await appApi<ActiveOperationsPolicy>(
    page,
    "/runtime-policies/operations",
  );
  const policyCleanup = registerCleanupAction({
    action: "restore-runtime-policy",
    policy: "operations",
    revision_id: originalPolicy.data.revision.id,
  });

  const collectorPolicy = {
    capability: "integration_read",
    effect: "read_only",
    idempotency: "safe",
    approval: "never",
    concurrency_group: "ops-patrol",
  } as const;
  const collector = (
    await appApi<McpServer>(page, "/integrations/mcp-servers", {
      method: "POST",
      body: {
        name: acceptanceId("ops-collector"),
        description: "Acceptance-owned deterministic Ops Patrol Collector",
        transport: "streamable_http",
        enabled: true,
        url: "http://opencitadel-ops-collector:8090/mcp",
        // The Collector's streamable-http endpoint mandates a bearer token
        // (OPS_COLLECTOR_TOKEN from .env.e2e, injected by the runner).
        headers: process.env.OPS_COLLECTOR_TOKEN
          ? { Authorization: `Bearer ${process.env.OPS_COLLECTOR_TOKEN}` }
          : undefined,
        visibility: "private",
        tool_policies: Object.fromEntries(
          [
            "get_capabilities",
            "k8s_workload_summary",
            "k8s_recent_events",
            "k8s_pod_logs",
            "prom_query",
            "http_probe",
            "certificate_status",
            "backup_status",
            "dependency_status",
          ].map((tool) => [tool, collectorPolicy]),
        ),
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "mcp-server",
    resource_id: collector.id,
    workspace_id: patrolTeamId,
  });

  try {
    await activateOperationsPolicy(
      page,
      (policy) => ({
        ...policy,
        patrol: {
          ...(policy.patrol ?? { remediation: "disabled" }),
          admission: "accepting",
        },
      }),
      "Acceptance: admit deterministic Patrol run",
    );
    await page.goto("/patrols/new");
    await page.locator("#patrol-name").fill(patrolPackName);
    await page.locator("#patrol-collector").click();
    const collectorOption = page.getByRole("option", {
      name: collector.name,
      exact: true,
    });
    await expect(collectorOption).toBeVisible({ timeout: 30_000 });
    await collectorOption.click();
    await page.locator("#patrol-template").click();
    await page
      .getByRole("option", { name: /Compose service baseline|Compose 服务基线/ })
      .click();
    await page.getByRole("button", { name: /Next|下一步/ }).click();
    // The wizard no longer pre-fills demo defaults; target_ref must match the
    // deterministic acceptance Collector (OPS_COLLECTOR_TARGET_REF).
    await page.locator("#patrol-target-ref").fill("opencitadel-local");
    await page.locator("#patrol-cluster").fill(acceptanceId("patrol-cluster"));
    await page.locator("#patrol-namespaces").fill("opencitadel");
    await page.getByRole("button", { name: /Next|下一步/ }).click();
    await expect(
      page.getByText(/OpenCitadel API health|OpenCitadel API 健康/),
    ).toBeVisible();
    await expect(page.getByText(/arbitrary URL|任意 URL/)).toHaveCount(0);
    await page.getByRole("button", { name: /Next|下一步/ }).click();
    await page.locator("#patrol-schedule-enabled").click();
    await page
      .getByRole("button", { name: /Create and dry run|创建并 dry-run/ })
      .click();

    await page.waitForURL(
      (url) =>
        /^\/patrols\/[0-9a-f-]{36}$/i.test(url.pathname) &&
        url.pathname !== "/patrols/new",
      { timeout: 120_000 },
    );
    patrolPackId = new URL(page.url()).pathname.split("/").at(-1) ?? "";
    expect(patrolPackId).not.toBe("");
    registerCleanupAction({
      action: "delete-resource",
      resource: "patrol-pack",
      resource_id: patrolPackId,
      workspace_id: patrolTeamId,
    });
    const pack = (
      await appApi<PatrolPack>(
        page,
        `/patrol-packs/${encodeURIComponent(patrolPackId)}`,
      )
    ).data;
    expect(pack).toMatchObject({
      id: patrolPackId,
      owner_user_id: operatorUserId,
      team_id: patrolTeamId,
      mcp_server_id: collector.id,
      status: "active",
    });
    expect(pack.validation_summary).toMatchObject({ ok: true });
    expect(pack.validation_summary.enabled_tools).toEqual(
      expect.arrayContaining([expect.any(String)]),
    );
    expect(pack.validation_summary.capability_hash).toMatch(/^[0-9a-f]{64}$/);

    await page.getByRole("button", { name: /Run now|立即运行/ }).click();
    await expect(page).toHaveURL(/\/patrol-runs\/[^/]+$/);
    patrolRunId = new URL(page.url()).pathname.split("/").at(-1) ?? "";
    const run = await pollProjection(
      async () =>
        (
          await appApi<PatrolRunDetail>(
            page,
            `/patrol-runs/${encodeURIComponent(patrolRunId)}`,
          )
        ).data,
      (candidate) =>
        ["completed", "completed_with_findings", "failed", "cancelled"].includes(
          candidate.status,
        ),
      { timeout: 5 * 60_000, message: "Patrol run must settle" },
    );
    expect(run.status).toBe("completed");
    expect(run.session_id).toEqual(expect.any(String));
    patrolSessionId = run.session_id as string;
    registerCleanupAction({
      action: "delete-resource",
      resource: "session",
      resource_id: patrolSessionId,
      workspace_id: patrolTeamId,
    });
    expect(run.check_results).toHaveLength(10);
    expect(run.check_results.every((result) => result.status === "pass")).toBe(
      true,
    );
    expect(run.check_results.every((result) => result.evidence_refs.length > 0)).toBe(
      true,
    );
    expect(run.counts).toMatchObject({ pass: 10, fail: 0, error: 0 });
    expect(run.evidence_completeness).toBe(1);

    await page.reload();
    await expect(page.getByText(/Completed|已完成/).first()).toBeVisible();
    await expect(
      page.getByRole("group", { name: / (Pass|通过)$/ }),
    ).toHaveCount(10);
    const download = page.waitForEvent("download");
    await page
      .getByRole("button", { name: /Download evidence package|下载证据包/ })
      .click();
    expect((await download).suggestedFilename()).toBe(
      `patrol-${patrolRunId}.zip`,
    );

    const profile = (
      await appApi<GovernanceProfile>(
        page,
        `/admin/governance/sessions/${encodeURIComponent(patrolSessionId)}/profile`,
      )
    ).data;
    expect(profile.chain.verified).toBe(true);
    expect(profile.runs).toContainEqual(
      expect.objectContaining({ family: "patrol", status: "completed" }),
    );
    expect(profile.activities).toContainEqual(
      expect.objectContaining({
        activity_type: "patrol.execute",
        status: "succeeded",
      }),
    );

    await setWorkspace(page);
    const personalPacks = await appApi<PatrolPackList>(page, "/patrol-packs");
    expect(personalPacks.data.items.map((item) => item.id)).not.toContain(
      patrolPackId,
    );
    await appApi(page, `/patrol-runs/${encodeURIComponent(patrolRunId)}`, {
      expectStatus: 404,
    });
    await setWorkspace(page, patrolTeamId);

    await activateOperationsPolicy(
      page,
      (policy) => ({
        ...policy,
        patrol: {
          ...(policy.patrol ?? { remediation: "disabled" }),
          admission: "paused",
        },
      }),
      "Acceptance: prove Patrol admission fails closed",
    );
    const blocked = await appApi(
      page,
      `/patrol-packs/${encodeURIComponent(patrolPackId)}/trigger`,
      {
        method: "POST",
        body: {},
        headers: { "Idempotency-Key": acceptanceId("patrol-paused") },
        expectStatus: 409,
      },
    );
    expect(blocked.errorKey).toBe("apiErrors.patrol.admissionPaused");
    await page.goto("/patrols/new");
    await expect(page.getByText(/paused|已暂停/)).toBeVisible();
    await page.goto(`/patrol-runs/${encodeURIComponent(patrolRunId)}`);
    await expect(
      page.getByRole("button", {
        name: /Download evidence package|下载证据包/,
      }),
    ).toBeVisible();
  } finally {
    await restoreOperationsPolicy(page, originalPolicy.data.revision.id);
    completeCleanupAction(policyCleanup);
  }
});

test("administration overview and governance preserve exact Patrol provenance", async ({
  operatorPage: page,
}) => {
  cover("ADM-OVERVIEW", "ADM-GOVERNANCE");
  await setWorkspace(page, patrolTeamId);

  const overview = (await appApi<AdminOverview>(page, "/admin/overview")).data;
  expect(overview.total_users).toBeGreaterThan(0);
  expect(overview.total_teams).toBeGreaterThan(0);
  expect(overview.total_sessions).toBeGreaterThan(0);
  await page.goto("/admin");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const governance = (
    await appApi<GovernanceOverview>(page, "/admin/governance/overview?days=30")
  ).data;
  expect(governance.chain.ok).toBe(true);
  expect(
    governance.patrol.reduce((total, item) => total + item.runs, 0),
  ).toBeGreaterThanOrEqual(1);

  const profile = (
    await appApi<GovernanceProfile>(
      page,
      `/admin/governance/sessions/${encodeURIComponent(patrolSessionId)}/profile`,
    )
  ).data;
  expect(profile.session.id).toBe(patrolSessionId);
  expect(profile.session.owner_user_id).toBe(operatorUserId);
  expect(profile.session.team_id).toBe(patrolTeamId);
  expect(profile.session.operator_scope).toBeNull();
  expect(profile.chain.verified).toBe(true);
  expect(profile.runs).toHaveLength(1);
  expect(profile.runs[0]).toMatchObject({ family: "patrol", status: "completed" });
  expect(profile.activities).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        run_id: profile.runs[0].run_id,
        activity_type: "patrol.execute",
        status: "succeeded",
      }),
    ]),
  );

  await page.goto("/admin/governance");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("audit chain and compliance report include the exact Patrol evidence", async ({
  operatorPage: page,
}) => {
  cover("ADM-AUDIT", "ADM-COMPLIANCE");
  await setWorkspace(page, patrolTeamId);

  const packAudit = (
    await appApi<AuditList>(
      page,
      `/admin/audit?resource_type=patrol_pack&resource_id=${encodeURIComponent(patrolPackId)}`,
    )
  ).data;
  expect(packAudit.total).toBeGreaterThanOrEqual(3);
  expect(packAudit.logs.every((log) => log.resource_id === patrolPackId)).toBe(
    true,
  );
  expect(packAudit.logs.every((log) => log.team_id === patrolTeamId)).toBe(true);
  expect(packAudit.logs.every((log) => log.actor_user_id === operatorUserId)).toBe(
    true,
  );
  expect(packAudit.logs.map((log) => log.action)).toEqual(
    expect.arrayContaining([
      "patrol_pack_created",
      "patrol_pack_validated",
      "patrol_pack_activated",
    ]),
  );

  const sessionChain = await appApi<{
    ok: boolean;
    session_id: string;
    session_entries: number;
  }>(
    page,
    `/admin/audit/verify-chain/sessions/${encodeURIComponent(patrolSessionId)}`,
  );
  expect(sessionChain.data).toMatchObject({
    ok: true,
    session_id: patrolSessionId,
  });
  expect(sessionChain.data.session_entries).toBeGreaterThan(0);

  const sessionAudit = (
    await appApi<AuditList>(
      page,
      `/admin/audit?session_id=${encodeURIComponent(patrolSessionId)}`,
    )
  ).data;
  expect(sessionAudit.total).toBeGreaterThanOrEqual(3);
  expect(sessionAudit.logs.every((log) => log.session_id === patrolSessionId)).toBe(
    true,
  );
  expect(sessionAudit.logs.every((log) => log.team_id === patrolTeamId)).toBe(true);
  expect(sessionAudit.logs.map((log) => log.action)).toEqual(
    expect.arrayContaining([
      "patrol_run_triggered",
      "patrol_result_submitted",
      "patrol_run_finalized",
    ]),
  );

  await page.goto("/admin/audit");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("button", { name: /Verify chain|验证链/ }).click();
  await expect(page.getByText(/Audit chain intact|审计链完整/).first()).toBeVisible();

  const evidence = (
    await appApi<{ sessions: EvidenceSessionItem[] }>(
      page,
      "/admin/evidence/sessions?limit=200",
    )
  ).data.sessions.find((item) => item.session_id === patrolSessionId);
  expect(evidence).toMatchObject({
    session_id: patrolSessionId,
    owner_user_id: operatorUserId,
    team_id: patrolTeamId,
    operator_scope: null,
    status: "completed",
    chain_ok: true,
  });

  const report = (
    await appApi<{ report: ComplianceReport }>(
      page,
      "/admin/compliance/report?format=json",
    )
  ).data.report;
  expect(report.chain_verification.ok).toBe(true);
  expect(report.controls.length).toBeGreaterThan(0);
  expect(report.summary.total).toBe(report.controls.length);

  await page.goto("/admin/compliance");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const evidenceRow = page.locator("tr", {
    has: page.locator(`a[href=\"/sessions/${patrolSessionId}\"]`),
  });
  await expect(evidenceRow).toBeVisible();
  await expect(evidenceRow.getByText(`team:${patrolTeamId}`)).toBeVisible();
  await page.goto("/admin/compliance/report");
  await page.getByRole("button", { name: /Generate report|生成报告/ }).click();
  await expect(page.getByText(/Report summary|报告摘要/)).toBeVisible();
});

test("mobile routes fit 390px and critical actions are keyboard operable", async ({
  operatorPage: page,
}) => {
  cover("UI-MOBILE", "UI-KEYBOARD");
  test.setTimeout(180_000);
  await setWorkspace(page, patrolTeamId);
  await page.setViewportSize({ width: 390, height: 844 });

  const routes = [
    "/",
    `/sessions/${encodeURIComponent(patrolSessionId)}`,
    "/knowledge",
    "/patrols",
    "/admin",
  ];
  for (const route of routes) {
    await page.goto(route);
    await expect(page.locator("body")).toBeVisible();
    await assertNoHorizontalOverflow(page, route);
  }

  await page.goto("/");
  const mobileNav = page.getByRole("navigation", {
    name: /Main navigation|主导航/,
  });
  await expect(mobileNav).toBeVisible();
  await expect(mobileNav.getByRole("link")).toHaveCount(3);
  const more = mobileNav.getByRole("button", { name: /More|更多/ });
  await tabToVisibleFocus(page, more, "mobile More action");
  await more.click();
  const settings = page.getByRole("button", {
    name: /System settings|系统设置/,
  });
  await tabToVisibleFocus(page, settings, "mobile Settings action");
  await settings.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await assertNoHorizontalOverflow(page, "settings dialog");
  await page.getByRole("button", { name: /Close|关闭/ }).click();

  await page.goto("/patrols");
  const createPack = page
    .getByRole("link", { name: /Create patrol|创建巡检/ })
    .first();
  await tabToVisibleFocus(page, createPack, "create Patrol Pack action");

  await page.goto("/admin/audit");
  const verifyChain = page.getByRole("button", {
    name: /Verify chain|验证链/,
  });
  await tabToVisibleFocus(page, verifyChain, "verify audit chain action");
});
