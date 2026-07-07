import { describe, expect, it } from "vitest";

import { resolveSafeRedirectPath } from "./safe-redirect";

describe("resolveSafeRedirectPath", () => {
  it("returns default for empty or unsafe values", () => {
    expect(resolveSafeRedirectPath(null)).toBe("/");
    expect(resolveSafeRedirectPath("")).toBe("/");
    expect(resolveSafeRedirectPath("//evil.com")).toBe("/");
    expect(resolveSafeRedirectPath("https://evil.com")).toBe("/");
  });

  it("allows same-origin relative paths", () => {
    expect(resolveSafeRedirectPath("/invitations/abc")).toBe("/invitations/abc");
    expect(resolveSafeRedirectPath("/invitations/abc?foo=bar")).toBe("/invitations/abc?foo=bar");
  });
});
