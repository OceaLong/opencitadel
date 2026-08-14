import { describe, expect, it } from "vitest";

import { controlStatusVariant } from "./compliance-status";

describe("controlStatusVariant", () => {
  it.each([
    ["pass", "success"],
    ["gap", "destructive"],
    ["attention", "warning"],
    ["not_verified", "secondary"],
    ["na", "secondary"],
    ["unknown", "secondary"],
  ] as const)("maps %s to %s", (status, expected) => {
    expect(controlStatusVariant(status)).toBe(expected);
  });
});
