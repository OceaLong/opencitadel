import { defineConfig } from "@playwright/test";

/** Bounded component security proof, independent of the business acceptance graph. */
export default defineConfig({
  testDir: ".",
  testMatch: /artifact-preview\.spec\.ts/,
  forbidOnly: true,
  fullyParallel: true,
  timeout: 30000,
  outputDir: "test-results/artifact-preview/artifacts",
  reporter: [
    ["line"],
    ["json", { outputFile: "test-results/artifact-preview/results.json" }],
  ],
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
});
