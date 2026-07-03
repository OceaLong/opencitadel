"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

import {
  IconAdmin,
  IconAudit,
  IconBack,
  IconInvitation,
  IconUsers,
} from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

const NAV = [
  { href: "/admin", labelKey: "overview" as const, icon: IconAdmin, exact: true },
  { href: "/admin/users", labelKey: "users" as const, icon: IconUsers },
  { href: "/admin/invitations", labelKey: "invitations" as const, icon: IconInvitation },
  { href: "/admin/audit", labelKey: "audit" as const, icon: IconAudit },
];

export function AdminLayoutShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const t = useTranslations("adminNav");
  const tCommon = useTranslations("common");
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        {tCommon("loading")}
      </div>
    );
  }

  if (user?.global_role !== "admin") {
    return (
      <div className="bg-background flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <p className="text-muted-foreground text-sm">{t("forbidden")}</p>
        <Button variant="outline" asChild>
          <Link href="/">{tCommon("backHome")}</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="bg-background flex min-h-screen flex-col">
      <header className="border-border/70 bg-card/80 flex items-center gap-4 border-b px-6 py-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <IconBack className="mr-1 size-4" />
            {t("back")}
          </Link>
        </Button>
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{t("title")}</h1>
          <p className="text-muted-foreground text-xs">{t("subtitle")}</p>
        </div>
      </header>
      <div className="flex flex-1">
        <nav className="border-border/70 bg-card/40 w-56 shrink-0 space-y-1.5 border-r p-4">
          {NAV.map(({ href, labelKey, icon: Icon, exact }) => {
            const active = exact ? pathname === href : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors",
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
        <main className="flex-1 overflow-auto p-6">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
