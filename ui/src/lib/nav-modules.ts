import { Stethoscope } from "lucide-react";
import type { ComponentType } from "react";

import {
  IconAdmin,
  IconAgent,
  IconAutomation,
  IconCodebase,
  IconKnowledge,
} from "@/lib/icons";

export type NavModuleKey =
  | "chat"
  | "patrol"
  | "automation"
  | "knowledge"
  | "codebase"
  | "admin";

export type NavModule = {
  key: NavModuleKey;
  href: string;
  icon: ComponentType<{ className?: string }>;
  match: (pathname: string) => boolean;
  /** feature flag 名，缺省表示恒可见 */
  requiresFlag?: "opsPatrolEnabled";
  /** 该模块是否有第二列上下文面板（Phase 2：chat 与 admin） */
  hasContextPanel?: boolean;
  /** 移动端底导航优先占格（spec §3.2：Chat/Patrol/Knowledge） */
  mobilePrimary?: boolean;
};

const prefixMatch = (prefix: string) => (pathname: string) =>
  pathname === prefix || pathname.startsWith(`${prefix}/`);

export const NAV_MODULES: NavModule[] = [
  {
    key: "chat",
    href: "/",
    icon: IconAgent,
    match: (pathname) => pathname === "/" || pathname.startsWith("/sessions/"),
    hasContextPanel: true,
    mobilePrimary: true,
  },
  {
    key: "patrol",
    href: "/patrols",
    icon: Stethoscope,
    match: (pathname) =>
      prefixMatch("/patrols")(pathname) || prefixMatch("/patrol-runs")(pathname),
    requiresFlag: "opsPatrolEnabled",
    hasContextPanel: true,
    mobilePrimary: true,
  },
  {
    key: "automation",
    href: "/automation",
    icon: IconAutomation,
    match: prefixMatch("/automation"),
  },
  {
    key: "knowledge",
    href: "/knowledge",
    icon: IconKnowledge,
    match: prefixMatch("/knowledge"),
    mobilePrimary: true,
  },
  {
    key: "codebase",
    href: "/codebase",
    icon: IconCodebase,
    match: prefixMatch("/codebase"),
  },
];

export const ADMIN_NAV: NavModule = {
  key: "admin",
  href: "/admin",
  icon: IconAdmin,
  match: prefixMatch("/admin"),
  hasContextPanel: true,
};

// 注意：/teams 故意不属于任何模块（rail 不放第六个图标，spec §3.1）——顶栏无标题属预期
export function matchModule(pathname: string): NavModule | undefined {
  if (ADMIN_NAV.match(pathname)) return ADMIN_NAV;
  return NAV_MODULES.find((module) => module.match(pathname));
}

export function splitMobileNav(modules: NavModule[]): {
  primary: NavModule[];
  overflow: NavModule[];
} {
  const preferred = modules.filter((m) => m.mobilePrimary);
  const backfill = modules.filter((m) => !m.mobilePrimary);
  const chosen = new Set(
    [...preferred, ...backfill].slice(0, 3).map((m) => m.key),
  );
  return {
    primary: modules.filter((m) => chosen.has(m.key)),
    overflow: modules.filter((m) => !chosen.has(m.key)),
  };
}
