import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatRelativeDate } from "./utils";

function toIsoDate(date: Date): string {
  return date.toISOString();
}

describe("formatRelativeDate", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-07T12:00:00"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns localized today label", () => {
    expect(formatRelativeDate(toIsoDate(new Date("2026-07-07T08:00:00")), "zh")).toBe("今天");
    expect(formatRelativeDate(toIsoDate(new Date("2026-07-07T08:00:00")), "en")).toBe("Today");
  });

  it("returns localized yesterday label", () => {
    expect(formatRelativeDate(toIsoDate(new Date("2026-07-06T08:00:00")), "zh")).toBe("昨天");
    expect(formatRelativeDate(toIsoDate(new Date("2026-07-06T08:00:00")), "en")).toBe("Yesterday");
  });

  it("returns localized weekday label within the past week", () => {
    expect(formatRelativeDate(toIsoDate(new Date("2026-07-03T08:00:00")), "zh")).toBe("周五");
    expect(formatRelativeDate(toIsoDate(new Date("2026-07-03T08:00:00")), "en")).toBe("Fri");
  });

  it("returns localized absolute date for entries older than a week", () => {
    expect(formatRelativeDate(toIsoDate(new Date("2026-06-20T08:00:00")), "zh")).toBe("6/20");
    expect(formatRelativeDate(toIsoDate(new Date("2026-06-20T08:00:00")), "en")).toBe("6/20");
  });

  it("falls back to today when date is missing or invalid", () => {
    expect(formatRelativeDate(null, "zh")).toBe("今天");
    expect(formatRelativeDate("invalid-date", "en")).toBe("Today");
  });
});
