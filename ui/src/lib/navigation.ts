export const NAVIGATION = [
  { href: "/", label: "运行" },
  { href: "/approvals", label: "审批" },
  { href: "/knowledge", label: "知识" },
  { href: "/settings", label: "推理与 MCP" },
  { href: "/teams", label: "团队" },
] as const;

export const RETIRED_ROUTE_FRAGMENTS = [
  "automation",
  "compliance",
  "memory",
  "patrol",
  "session",
  "share",
  "skill",
] as const;

export function isActivePath(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" || pathname.startsWith("/runs/") : pathname === href;
}
