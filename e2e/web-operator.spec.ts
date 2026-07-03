import { test, expect } from "@playwright/test";

/**
 * End-to-end smoke for governed Web Operator against demo OpsConsole.
 * Requires: docker compose --profile local --profile demo up
 *           OpenCitadel at PLAYWRIGHT_BASE_URL (default http://localhost:8088)
 */
const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8088";
const OPS_CONSOLE = process.env.OPS_CONSOLE_URL ?? "http://localhost:9099";

test.describe("OpsConsole demo backend", () => {
  test("login page renders", async ({ page }) => {
    await page.goto(`${OPS_CONSOLE}/login`);
    await expect(page.locator("#login-form")).toBeVisible();
    await expect(page.locator("#username")).toHaveValue("agent");
  });

  test("can login and view tickets", async ({ page }) => {
    await page.goto(`${OPS_CONSOLE}/login`);
    await page.locator("#btn-login").click();
    await expect(page).toHaveURL(/\/tickets/);
    await expect(page.locator("#ticket-table")).toBeVisible();
  });
});

test.describe("OpenCitadel platform", () => {
  test("home page loads", async ({ page }) => {
    await page.goto(BASE);
    await expect(page.locator("body")).toBeVisible();
  });
});
