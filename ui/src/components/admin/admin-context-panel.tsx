"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { AccountMenu } from "@/components/account-menu";
import { Sidebar, SidebarContent, SidebarHeader, SidebarTrigger } from "@/components/ui/sidebar";

import { ADMIN_NAV_ITEMS } from "@/lib/admin-nav";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

export function AdminContextPanel() {
  const pathname = usePathname();
  const t = useTranslations("adminNav");
  const { user } = useAuth();
  const isAdmin = user?.global_role === "admin";
  const isAuditor = user?.global_role === "auditor";

  if (!isAdmin && !isAuditor) return null;

  const visibleNav = ADMIN_NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);

  return (
    <Sidebar>
      <SidebarHeader>
        <SidebarTrigger className="cursor-pointer" />
        {isAuditor ? (
          <p className="text-muted-foreground px-2 text-xs">{t("auditorSubtitle")}</p>
        ) : null}
      </SidebarHeader>
      <SidebarContent className="p-2">
        <nav className="space-y-1">
          {visibleNav.map(({ href, labelKey, icon: Icon, exact }) => {
            const active = exact ? pathname === href : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-muted text-foreground font-medium"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                {t(labelKey)}
              </Link>
            );
          })}
        </nav>
      </SidebarContent>
      <div className="md:hidden">
        <AccountMenu />
      </div>
    </Sidebar>
  );
}
