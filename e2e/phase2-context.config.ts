import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";

const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!root || process.env.ACCEPTANCE_PHASE2_CONTEXT !== "1")
  throw new Error(
    "Phase 2 context profile requires the owned acceptance runner",
  );

/** Focused real-stack evidence. The release AC01–AC22 graph uses playwright.config.ts. */
export default defineConfig({
  testDir: ".",
  testIgnore: "**/tmp/**",
  timeout: 120_000,
  forbidOnly: true,
  workers: 1,
  outputDir: resolve(root, "playwright/artifacts"),
  reporter: [
    ["line"],
    ["junit", { outputFile: resolve(root, "playwright/junit.xml") }],
    ["json", { outputFile: resolve(root, "playwright/native-results.json") }],
  ],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "bootstrap",
      testMatch: /bootstrap\.setup\.ts/,
      teardown: "cleanup",
    },
    {
      name: "phase2-context",
      dependencies: ["bootstrap"],
      testMatch: /long-running-context\.spec\.ts/,
    },
    { name: "cleanup", testMatch: /cleanup\.setup\.ts/ },
  ],
});
