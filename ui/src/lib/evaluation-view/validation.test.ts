import { expect, test } from "vitest";

import { canApplyImport, matrixQuantity } from "./validation";
test("errors and stale draft revisions block apply", () => {
  expect(canApplyImport(1, true)).toBe(false);
  expect(canApplyImport(0, false)).toBe(false);
  expect(canApplyImport(0, true)).toBe(true);
});
test("matrix limits apply to total results, including repeats", () => {
  expect(matrixQuantity(1000, 5, 1)).toBe(5000);
  expect(matrixQuantity(1000, 5, 5)).toBeNull();
  expect(matrixQuantity(1, 0, 1)).toBeNull();
  expect(matrixQuantity(1, 1, 1.5)).toBeNull();
});
