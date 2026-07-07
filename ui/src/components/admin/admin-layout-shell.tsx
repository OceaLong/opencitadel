"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import {
  IconAdmin,
  IconAudit,
  IconBack,
  IconInvitation,
  IconUsers,
} from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

type NavItem = {
  href: string;
  labelKey: string;
  icon: typeof IconAdmin;
  exact?: boolean;
  adminOnly?: boolean;
};

const NAV: NavItem[] = [
  { href: "/admin", labelKey: "overview", icon: IconAdmin, exact: true },
  { href: "/admin/users", labelKey: "users", icon: IconUsers, adminOnly: true },
  { href: "/admin/teams", labelKey: "teams", icon: IconUsers, adminOnly: true },
  {
    href: "/admin/invitations",
    labelKey: "invitations",
    icon: IconInvitation,
    adminOnly: true,
  },
  { href: "/admin/audit", labelKey: "audit", icon: IconAudit },
  { href: "/admin/compliance", labelKey: "evidence", icon: IconAudit, exact: true },
  { href: "/admin/compliance/report", labelKey: "complianceReport", icon: IconAudit },
];

function AdminNavLinks({
  pathname,
  items,
  onNavigate,
  className,
}: {
  pathname: string;
  items: NavItem[];
  onNavigate?: () => void;
  className?: string;
}) {
  const t = useTranslations("adminNav");

  return (
    <nav className={cn("space-y-1.5", className)}>
      {items.map(({ href, labelKey, icon: Icon, exact }) => {
        const active = exact ? pathname === href : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            className={cn(
              "flex min-h-11 items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors",
              active
                ? "bg-primary text-primary-foreground shadow-[var(--shadow-card)]"
                : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
            )}
          >
            <Icon className="size-4" />
            {t(labelKey)}
          </Link>
        );
      })}
    </nav>
  );
}

export function AdminLayoutShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const t = useTranslations("adminNav");
  const tCommon = useTranslations("common");
  const { user, loading } = useAuth();
  const [navOpen, setNavOpen] = useState(false);

  const isAdmin = user?.global_role === "admin";
  const isAuditor = user?.global_role === "auditor";
  const canAccess = isAdmin || isAuditor;

  if (loading) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        {tCommon("loading")}
      </div>
    );
  }

  if (!canAccess) {
    return (
      <div className="bg-background flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <p className="text-muted-foreground text-sm">{t("forbidden")}</p>
        <Button variant="outline" asChild>
          <Link href="/">{tCommon("backHome")}</Link>
        </Button>
      </div>
    );
  }

  const visibleNav = NAV.filter((item) => !item.adminOnly || isAdmin);

  return (
    <div className="bg-background flex h-screen flex-col overflow-hidden">
      <header className="border-border/70 bg-card/80 flex items-center gap-3 border-b px-4 py-3 md:gap-4 md:px-6 md:py-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <IconBack className="mr-1 size-4" />
            <span className="hidden sm:inline">{t("back")}</span>
          </Link>
        </Button>
        <p className="text-muted-foreground min-w-0 flex-1 truncate text-sm">
          {isAuditor ? t("auditorSubtitle") : t("subtitle")}
        </p>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="md:hidden"
          aria-label={t("menu")}
          onClick={() => setNavOpen(true)}
        >
          <Menu className="size-4" />
        </Button>
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className="border-border/70 bg-card/40 hidden w-56 shrink-0 border-r p-4 md:block">
          <AdminNavLinks pathname={pathname} items={visibleNav} />
        </aside>
        <main className="min-h-0 flex-1 overflow-auto p-4 md:p-6">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>

      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent side="left" className="w-72 p-4">
          <SheetHeader>
            <SheetTitle>{t("menu")}</SheetTitle>
          </SheetHeader>
          <AdminNavLinks
            pathname={pathname}
            items={visibleNav}
            onNavigate={() => setNavOpen(false)}
            className="mt-4"
          />
        </SheetContent>
      </Sheet>
    </div>
  );
}
