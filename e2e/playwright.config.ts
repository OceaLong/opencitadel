import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 120_000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8088",
    trace: "on-first-retry",
  },
});
