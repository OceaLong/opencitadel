import { expect, test } from "@playwright/test";

import { acceptanceId } from "../support/ids";

test("combines a validated run ID and suffix", () => {
  expect(acceptanceId("endpoint", { ACCEPTANCE_RUN_ID: "run-a1" })).toBe(
    "run-a1-endpoint",
  );
});

test("rejects missing or unsafe namespace components", () => {
  expect(() => acceptanceId("endpoint", {})).toThrow(/ACCEPTANCE_RUN_ID/);
  expect(() =>
    acceptanceId("shell;command", { ACCEPTANCE_RUN_ID: "run-a1" }),
  ).toThrow(/suffix/);
  expect(() =>
    acceptanceId("endpoint", { ACCEPTANCE_RUN_ID: "UPPERCASE" }),
  ).toThrow(/ACCEPTANCE_RUN_ID/);
});
