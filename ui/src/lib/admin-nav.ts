import {
  IconAdmin,
  IconAudit,
  IconInvitation,
  IconSecurity,
  IconUsers,
} from "@/lib/icons";

export type AdminNavItem = {
  href: string;
  labelKey: string;
  icon: typeof IconAdmin;
  exact?: boolean;
  adminOnly?: boolean;
};

export const ADMIN_NAV_ITEMS: AdminNavItem[] = [
  { href: "/admin", labelKey: "overview", icon: IconAdmin, exact: true },
  { href: "/admin/users", labelKey: "users", icon: IconUsers, adminOnly: true },
  { href: "/admin/teams", labelKey: "teams", icon: IconUsers, adminOnly: true },
  { href: "/admin/invitations", labelKey: "invitations", icon: IconInvitation, adminOnly: true },
  { href: "/admin/audit", labelKey: "audit", icon: IconAudit },
  { href: "/admin/governance", labelKey: "governance", icon: IconSecurity },
  { href: "/admin/compliance", labelKey: "evidence", icon: IconAudit, exact: true },
  { href: "/admin/compliance/report", labelKey: "complianceReport", icon: IconAudit },
];

/** Path-boundary prefix match: `/admin/foo` matches `/admin/foo` and
 * `/admin/foo/bar`, but not `/admin/foobar`. */
function isBoundaryPrefix(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function matchAdminNav(pathname: string): AdminNavItem | undefined {
  const exact = ADMIN_NAV_ITEMS.find((item) => item.exact && pathname === item.href);
  if (exact) return exact;
  const prefixMatch = ADMIN_NAV_ITEMS.filter(
    (item) => !item.exact && isBoundaryPrefix(pathname, item.href),
  ).sort((a, b) => b.href.length - a.href.length)[0];
  if (prefixMatch) return prefixMatch;
  // Neither pass matched (e.g. a nested detail route like
  // /admin/compliance/sessions/<id> under an `exact` parent). Fall back to
  // the longest boundary-prefix match across ALL items, exact ones included.
  return ADMIN_NAV_ITEMS.filter((item) => isBoundaryPrefix(pathname, item.href)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
}
