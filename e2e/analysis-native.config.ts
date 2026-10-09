import { defineConfig } from "@playwright/test";
/** Isolated mocked native A04 UI evidence; this is not full-stack acceptance. */
export default defineConfig({
  testDir: ".",
  testMatch: /analysis-native\.spec\.ts/,
  forbidOnly: true,
  workers: 1,
  timeout: 60_000,
  outputDir:
    "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/artifacts",
  reporter: [
    ["line"],
    [
      "json",
      {
        outputFile:
          "../.superpowers/sdd/2026-09-07-execution-visualization-plan/A04-browser/results.json",
      },
    ],
  ],
  use: {
    baseURL: "http://127.0.0.1:3184",
    trace: "on",
    screenshot: "on",
    headless: true,
    launchOptions: {
      executablePath:
        "/Users/longhaiyang/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    },
  },
});
