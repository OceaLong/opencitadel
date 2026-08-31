import { expect, type Page, test as base } from "@playwright/test";

import { appApi } from "../support/api";
import {
  readBootstrapState,
  type BootstrapState,
} from "../support/bootstrap-state";

type AcceptanceFixtures = {
  operatorPage: Page;
  bootstrapState: BootstrapState;
};

type UserResponse = {
  email: string;
  global_role: string;
};

async function loginAsAcceptanceAdmin(page: Page): Promise<void> {
  const email = process.env.BOOTSTRAP_ADMIN_EMAIL;
  const password = process.env.BOOTSTRAP_ADMIN_PASSWORD;
  if (!email || !password) {
    throw new Error("BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD are required");
  }
  await page.goto("/login");
  const login = await appApi<UserResponse>(page, "/auth/login", {
    method: "POST",
    body: { email_or_username: email, password },
  });
  expect(login.data.email).toBe(email);
  expect(login.data.global_role).toBe("admin");
  await page.goto("/");
  await expect(page).not.toHaveURL(/\/login(?:\?|$)/);
}

export const test = base.extend<AcceptanceFixtures>({
  operatorPage: async ({ page }, use) => {
    await loginAsAcceptanceAdmin(page);
    await use(page);
  },
  bootstrapState: async ({}, use) => {
    const state = readBootstrapState();
    if (!state || state.cleanup_completed) {
      throw new Error("active acceptance bootstrap state is required");
    }
    await use(state);
  },
});

export { appApi, expect };
