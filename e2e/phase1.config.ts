import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";

const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!root || process.env.ACCEPTANCE_PHASE1_BROWSER !== "1")
  throw new Error(
    "Phase 1 browser profile requires the owned acceptance runner",
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
      name: "phase1-execution",
      dependencies: ["bootstrap"],
      testMatch: /execution-workbench\.spec\.ts/,
      grep: /real completed shell write loses its receipt and stays durably unknown without retry|phase 1 drift refresh preserves the Run and requires a new exact approval before writing/,
    },
    { name: "cleanup", testMatch: /cleanup\.setup\.ts/ },
  ],
});
