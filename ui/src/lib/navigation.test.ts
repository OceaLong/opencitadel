import { describe, expect, it } from "vitest";

import { isActivePath, NAVIGATION, RETIRED_ROUTE_FRAGMENTS } from "./navigation";

describe("greenfield navigation", () => {
  it("contains only retained product roots", () => {
    expect(NAVIGATION.map((item) => item.href)).toEqual([
      "/",
      "/approvals",
      "/knowledge",
      "/settings",
      "/teams",
    ]);
    expect(NAVIGATION.some((item) => RETIRED_ROUTE_FRAGMENTS.some((value) => item.href.includes(value)))).toBe(false);
  });

  it("treats run detail as part of the Run workspace", () => {
    expect(isActivePath("/runs/123", "/")).toBe(true);
    expect(isActivePath("/approvals", "/approvals")).toBe(true);
  });
});
