import { expect, type Page, test as base } from "@playwright/test";

type PatrolFixtures = {
  operatorPage: Page;
};

export const test = base.extend<PatrolFixtures>({
  operatorPage: async ({ page }, use) => {
    const password = process.env.PATROL_E2E_PASSWORD;
    if (!password) {
      throw new Error("PATROL_E2E_PASSWORD required");
    }
    await page.goto("/login");
    const inputs = page.locator("form input");
    await inputs.nth(0).fill(process.env.PATROL_E2E_OPERATOR ?? "admin@example.com");
    await inputs.nth(1).fill(password);
    await page.locator("form button").first().click();
    await expect(page).not.toHaveURL(/\/login/);
    await use(page);
  },
});

export { expect };

export async function appApi<T>(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  return page.evaluate(
    async ({ requestPath, requestInit }) => {
      const csrf = document.cookie
        .split("; ")
        .find((item) => item.startsWith("csrf_token="))
        ?.split("=")
        .slice(1)
        .join("=");
      const response = await fetch(`/api${requestPath}`, {
        method: requestInit.method ?? "GET",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
        },
        body: requestInit.body === undefined ? undefined : JSON.stringify(requestInit.body),
      });
      const payload = await response.json();
      if (!response.ok || payload.code !== 0) {
        throw new Error(`${requestInit.method ?? "GET"} ${requestPath}: ${payload.msg}`);
      }
      return payload.data;
    },
    { requestPath: path, requestInit: init },
  );
}
