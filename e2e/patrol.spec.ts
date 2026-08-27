import { appApi, expect, test } from "./patrol.fixture";
import type { components } from "../ui/src/lib/api/generated/schema";

const enabled = process.env.PATROL_E2E === "1";

type ActiveOperationsPolicy = components["schemas"]["ActiveOperationsPolicyResponse"];
type OperationsPolicy = components["schemas"]["OperationsPolicy-Input"];

async function activateOperationsPolicy(
  page: Parameters<typeof appApi>[0],
  update: (policy: OperationsPolicy) => OperationsPolicy,
  note: string,
): Promise<ActiveOperationsPolicy> {
  const active = await appApi<ActiveOperationsPolicy>(page, "/runtime-policies/operations");
  return appApi<ActiveOperationsPolicy>(page, "/runtime-policies/operations/revisions", {
    method: "POST",
    body: {
      expected_head_version: active.head.version,
      expected_active_revision_id: active.revision.id,
      policy: update(active.revision.policy),
      note,
    },
  });
}

test.describe("Ops Patrol real runtime", () => {
  test.skip(!enabled, "Set PATROL_E2E=1 for a stack with the execution kernel, Collector, and a tool-capable model");

  test("operator creates, validates, runs, reviews evidence, and rolls back safely", async ({
    operatorPage: page,
  }) => {
    test.setTimeout(10 * 60_000);
    const original = await appApi<ActiveOperationsPolicy>(
      page,
      "/runtime-policies/operations",
    );
    await activateOperationsPolicy(
      page,
      (policy) => ({ ...policy, patrol: { ...policy.patrol, admission: "accepting" } }),
      "E2E: accept patrol runs",
    );
    const integrations = await appApi<components["schemas"]["MCPServerListResponse"]>(
      page,
      "/integrations/mcp-servers",
    );
    const collector = integrations.items.find((server) => server.name === "ops-collector");
    if (!collector) throw new Error("ops-collector Integration is missing");
    await appApi(page, `/integrations/mcp-servers/${encodeURIComponent(collector.id)}/enabled`, {
      method: "PATCH",
      body: { enabled: true },
    });

    let runUrl = "";
    try {
      await page.goto("/patrols/new");
      await page.locator("#patrol-name").fill("E2E daily patrol");
      await page.locator("#patrol-collector").click();
      await page.getByRole("option", { name: /ops-collector/i }).click();
      await page.getByRole("button", { name: /Next|下一步/ }).click();
      await page.locator("#patrol-cluster").fill("opencitadel-patrol-e2e");
      await page.locator("#patrol-namespace").fill("opencitadel");
      await page.getByRole("button", { name: /Next|下一步/ }).click();
      await expect(page.getByText(/Workload availability|工作负载可用性/)).toBeVisible();
      await expect(page.getByText(/arbitrary URL|任意 URL/)).not.toBeVisible();
      await page.getByRole("button", { name: /Next|下一步/ }).click();
      await page.locator("#patrol-schedule-enabled").click();
      await page.getByRole("button", { name: /Create and dry run|创建并 dry-run/ }).click();

      await expect(page).toHaveURL(/\/patrols\/[^/]+$/, { timeout: 120_000 });
      await expect(page.getByText(/Active|已激活/)).toBeVisible();
      await page.getByRole("button", { name: /Run now|立即运行/ }).click();
      await expect(page).toHaveURL(/\/patrol-runs\/[^/]+$/);
      runUrl = page.url();
      await expect(page.getByText(/Completed|已完成/).first()).toBeVisible({ timeout: 5 * 60_000 });
      await expect(page.getByText(/PASS 10/)).toBeVisible();

      const download = page.waitForEvent("download");
      await page.getByRole("button", { name: /Download evidence package|下载证据包/ }).click();
      expect((await download).suggestedFilename()).toMatch(/^patrol-.+\.zip$/);

      const falsePositive = page.getByRole("button", { name: /False positive|误报/ }).first();
      if (await falsePositive.isVisible()) {
        await falsePositive.click();
        await page.getByPlaceholder(/reason|原因/i).fill("E2E injected condition");
        await page.getByRole("button", { name: /Confirm|确认/ }).click();
      }

      await page.setViewportSize({ width: 390, height: 844 });
      await page.reload();
      expect(await page.locator("body").evaluate((body) => body.scrollWidth <= 390)).toBe(true);

      await activateOperationsPolicy(
        page,
        (policy) => ({ ...policy, patrol: { ...policy.patrol, admission: "paused" } }),
        "E2E: verify paused patrol admission",
      );
      await page.goto("/patrols/new");
      await expect(page.getByText(/paused|已暂停/)).toBeVisible();
      await page.goto(runUrl);
      await expect(page.getByRole("button", { name: /Download evidence package|下载证据包/ })).toBeVisible();
      await page.goto("/");
      await expect(page.locator("body")).toBeVisible();
    } finally {
      await activateOperationsPolicy(
        page,
        () => original.revision.policy,
        "E2E: restore original Operations Policy",
      );
    }
  });
});
