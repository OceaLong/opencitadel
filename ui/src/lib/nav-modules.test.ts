import { describe, expect, it } from "vitest";

import { ADMIN_NAV, matchModule, NAV_MODULES, splitMobileNav } from "./nav-modules";

describe("NAV_MODULES", () => {
  it("keeps rail order chat/patrol/automation/knowledge/codebase", () => {
    expect(NAV_MODULES.map((m) => m.key)).toEqual([
      "chat",
      "patrol",
      "automation",
      "knowledge",
      "codebase",
    ]);
  });

  it("matches chat for / and /sessions/*", () => {
    expect(matchModule("/")?.key).toBe("chat");
    expect(matchModule("/sessions/abc")?.key).toBe("chat");
  });

  it("matches each module by its prefix", () => {
    expect(matchModule("/patrols")?.key).toBe("patrol");
    expect(matchModule("/patrol-runs/xyz")?.key).toBe("patrol");
    expect(matchModule("/automation")?.key).toBe("automation");
    expect(matchModule("/knowledge")?.key).toBe("knowledge");
    expect(matchModule("/codebase")?.key).toBe("codebase");
    expect(matchModule("/admin/users")?.key).toBe("admin");
    expect(matchModule("/teams")).toBeUndefined();
  });

  it("splitMobileNav prefers mobilePrimary modules: chat/patrol/knowledge", () => {
    const { primary, overflow } = splitMobileNav(NAV_MODULES);
    expect(primary.map((m) => m.key)).toEqual(["chat", "patrol", "knowledge"]);
    expect(overflow.map((m) => m.key)).toEqual(["automation", "codebase"]);
  });

  it("splitMobileNav backfills in nav order when patrol is filtered out", () => {
    const filtered = NAV_MODULES.filter((m) => m.key !== "patrol");
    const { primary, overflow } = splitMobileNav(filtered);
    expect(primary.map((m) => m.key)).toEqual(["chat", "automation", "knowledge"]);
    expect(overflow.map((m) => m.key)).toEqual(["codebase"]);
  });

  it("admin nav matches /admin and is flagged with roles", () => {
    expect(ADMIN_NAV.match("/admin")).toBe(true);
    expect(ADMIN_NAV.match("/administrator")).toBe(false);
  });
});
