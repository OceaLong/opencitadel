import { appApi, expect, test } from "./patrol.fixture";

const enabled = process.env.PATROL_E2E === "1";

test.describe("Ops Patrol real runtime", () => {
  test.skip(!enabled, "Set PATROL_E2E=1 for a stack with Worker, Collector, and a tool-capable model");

  test("operator creates, validates, runs, reviews evidence, and rolls back safely", async ({
    operatorPage: page,
  }) => {
    test.setTimeout(10 * 60_000);
    const flags = await appApi<Record<string, boolean>>(page, "/app-config/sections/feature_flags");
    await appApi(page, "/app-config/sections/feature_flags", {
      method: "PUT",
      body: { ...flags, enable_ops_patrol: true },
    });
    await appApi(page, "/app-config/mcp-servers/ops-collector/enabled", {
      method: "POST",
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

      await appApi(page, "/app-config/sections/feature_flags", {
        method: "PUT",
        body: { ...flags, enable_ops_patrol: false },
      });
      await page.goto("/patrols/new");
      await expect(page.getByText(/not enabled|未启用/)).toBeVisible();
      await page.goto(runUrl);
      await expect(page.getByRole("button", { name: /Download evidence package|下载证据包/ })).toBeVisible();
      await page.goto("/");
      await expect(page.locator("body")).toBeVisible();
    } finally {
      await appApi(page, "/app-config/sections/feature_flags", {
        method: "PUT",
        body: flags,
      });
    }
  });
});
