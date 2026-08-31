import { resolve } from "node:path";

import { defineConfig } from "@playwright/test";

const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR ?? resolve("test-results");

export default defineConfig({
  testDir: ".",
  timeout: 120_000,
  forbidOnly: true,
  fullyParallel: true,
  outputDir: resolve(evidenceDir, "playwright/artifacts"),
  reporter: [
    ["line"],
    ["junit", { outputFile: resolve(evidenceDir, "playwright/junit.xml") }],
    ["json", { outputFile: resolve(evidenceDir, "playwright/native-results.json") }],
    ["./reporters/zero-skip-reporter.ts"],
  ],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8088",
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
      name: "identity",
      dependencies: ["bootstrap"],
      testMatch: /identity\.spec\.ts/,
    },
    {
      name: "control-plane",
      dependencies: ["bootstrap"],
      testMatch: /control-plane\.spec\.ts/,
    },
    {
      name: "resources",
      dependencies: ["control-plane"],
      testMatch: /resources\.spec\.ts/,
    },
    {
      name: "execution",
      dependencies: ["bootstrap"],
      testMatch: /execution\.spec\.ts/,
    },
    {
      name: "patrol-admin",
      dependencies: ["control-plane"],
      testMatch: [/patrol-admin\.spec\.ts/, /web-operator\.spec\.ts/],
    },
    {
      name: "cleanup",
      testMatch: /cleanup\.setup\.ts/,
    },
  ],
});
