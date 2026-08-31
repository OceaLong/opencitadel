import { expect, test } from "@playwright/test";

import {
  evaluateAcceptance,
  type AcceptanceRecord,
} from "../reporters/zero-skip-reporter";

const REQUIRED = ["ID-LOGIN", "INF-ENDPOINT"] as const;

function record(
  requirementId: string,
  status: AcceptanceRecord["status"] = "passed",
  testId = `test-${requirementId}`,
): AcceptanceRecord {
  return {
    requirementId,
    testId,
    project: requirementId.startsWith("ID-") ? "identity" : "control-plane",
    status,
    durationMs: 1,
  };
}

test("accepts an exact, passing requirement set", () => {
  const result = evaluateAcceptance(
    [record("ID-LOGIN"), record("INF-ENDPOINT")],
    REQUIRED,
  );

  expect(result.errors).toEqual([]);
  expect(result.coverage).toHaveLength(2);
});

for (const status of ["skipped", "interrupted"] as const) {
  test(`rejects a ${status} acceptance result`, () => {
    const result = evaluateAcceptance(
      [record("ID-LOGIN", status), record("INF-ENDPOINT")],
      REQUIRED,
    );

    expect(result.errors).toContain(`acceptance test test-ID-LOGIN was ${status}`);
  });
}

test("rejects duplicate requirement IDs", () => {
  const result = evaluateAcceptance(
    [record("ID-LOGIN"), record("ID-LOGIN", "passed", "duplicate"), record("INF-ENDPOINT")],
    REQUIRED,
  );

  expect(result.errors).toContain("acceptance requirement ID-LOGIN is covered 2 times");
});

test("rejects missing and unknown requirement IDs", () => {
  const result = evaluateAcceptance(
    [record("ID-LOGIN"), record("NOT-A-REQUIREMENT")],
    REQUIRED,
  );

  expect(result.errors).toContain("acceptance requirement INF-ENDPOINT is missing");
  expect(result.errors).toContain("unknown acceptance requirement NOT-A-REQUIREMENT");
});
