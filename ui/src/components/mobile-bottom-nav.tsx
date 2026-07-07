"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import {
  IconAdmin,
  IconAgent,
  IconAutomation,
  IconCodebase,
  IconKnowledge,
  IconMarketplace,
  IconMore,
  IconSettings,
  IconUsers,
} from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

type NavItem = {
  href: string;
  labelKey: "chat" | "codebase" | "knowledge" | "marketplace";
  icon: typeof IconAgent;
  match: (pathname: string) => boolean;
};

const MAIN_NAV: NavItem[] = [
  {
    href: "/",
    labelKey: "chat",
    icon: IconAgent,
    match: (pathname) => pathname === "/" || pathname.startsWith("/sessions/"),
  },
  {
    href: "/codebase",
    labelKey: "codebase",
    icon: IconCodebase,
    match: (pathname) => pathname.startsWith("/codebase"),
  },
  {
    href: "/knowledge",
    labelKey: "knowledge",
    icon: IconKnowledge,
    match: (pathname) => pathname.startsWith("/knowledge"),
  },
  {
    href: "/marketplace",
    labelKey: "marketplace",
    icon: IconMarketplace,
    match: (pathname) => pathname.startsWith("/marketplace"),
  },
];

export function MobileBottomNav() {
  const pathname = usePathname();
  const t = useTranslations("mobileNav");
  const tAccount = useTranslations("account");
  const { user } = useAuth();
  const { openSettings } = useSettingsDialog();
  const [moreOpen, setMoreOpen] = useState(false);

  const isAdmin = user?.global_role === "admin";
  const isAuditor = user?.global_role === "auditor";

  return (
    <>
      <nav
        className="border-border/70 bg-background/95 pb-safe fixed inset-x-0 bottom-0 z-50 border-t backdrop-blur md:hidden"
        aria-label={t("label")}
      >
        <div className="grid h-14 grid-cols-5">
          {MAIN_NAV.map(({ href, labelKey, icon: Icon, match }) => {
            const active = match(pathname);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex min-h-11 flex-col items-center justify-center gap-0.5 px-1 text-[10px] transition-colors",
                  active
                    ? "text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="size-5 shrink-0" />
                <span className="truncate">{t(labelKey)}</span>
              </Link>
            );
          })}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            className="text-muted-foreground hover:text-foreground flex min-h-11 flex-col items-center justify-center gap-0.5 px-1 text-[10px] transition-colors"
          >
            <IconMore className="size-5 shrink-0" />
            <span className="truncate">{t("more")}</span>
          </button>
        </div>
      </nav>

      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent side="bottom" className="pb-safe rounded-t-2xl">
          <SheetHeader>
            <SheetTitle>{t("more")}</SheetTitle>
          </SheetHeader>
          <div className="mt-4 grid gap-2">
            <Button variant="outline" className="h-11 justify-start" asChild>
              <Link href="/automation" onClick={() => setMoreOpen(false)}>
                <IconAutomation className="size-4" />
                {t("automation")}
              </Link>
            </Button>
            <Button variant="outline" className="h-11 justify-start" asChild>
              <Link href="/teams" onClick={() => setMoreOpen(false)}>
                <IconUsers className="size-4" />
                {t("teams")}
              </Link>
            </Button>
            <Button
              variant="outline"
              className="h-11 justify-start"
              onClick={() => {
                setMoreOpen(false);
                openSettings("models-setting");
              }}
            >
              <IconSettings className="size-4" />
              {tAccount("settings")}
            </Button>
            {(isAdmin || isAuditor) && (
              <Button variant="outline" className="h-11 justify-start" asChild>
                <Link href="/admin" onClick={() => setMoreOpen(false)}>
                  <IconAdmin className="size-4" />
                  {tAccount("adminPanel")}
                </Link>
              </Button>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
