import { expect, test } from "vitest";

import { fromUtcInput, recentRange, utcInput } from "./time-range";
test("range shortcuts retain exact UTC bounds and UTC date controls round-trip", () => {
  const now = new Date("2026-09-09T08:15:00Z");
  expect(recentRange(7, now)).toEqual({
    start: "2026-09-02T08:15:00.000Z",
    end: "2026-09-09T08:15:00.000Z",
  });
  expect(fromUtcInput(utcInput(now.toISOString()))).toBe(now.toISOString());
  expect(utcInput("invalid")).toBe("");
  expect(fromUtcInput("")).toBeUndefined();
});
