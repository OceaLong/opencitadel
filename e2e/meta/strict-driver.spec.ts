import { expect, test } from "@playwright/test";
import { assertStrictReceipt } from "../support/strict-driver";

test("strict receipt requires current invocation, complete requirements and restored kernel", () => {
  const binding = {
    invocation_id: "synthetic-validator-only",
    project: "owned-test",
  };
  const value = {
    status: "passed",
    kernel_restored: true,
    binding,
    requirements: ["AC02", "AC05", "AC12", "AC13", "AC19", "AC22"],
  };
  expect(() => assertStrictReceipt(value, binding)).not.toThrow();
  for (const invalid of [
    null,
    {},
    { ...value, status: "skipped" },
    { ...value, kernel_restored: false },
    { ...value, binding: { ...binding, invocation_id: "stale" } },
    { ...value, requirements: ["AC02"] },
  ]) {
    expect(() => assertStrictReceipt(invalid, binding)).toThrow(
      /incomplete|another invocation/,
    );
  }
});
