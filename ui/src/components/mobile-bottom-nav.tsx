"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { LogIn, LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

import { useNavModules } from "@/hooks/use-nav-modules";
import { IconAdmin, IconMore, IconSettings, IconUsers } from "@/lib/icons";
import { splitMobileNav } from "@/lib/nav-modules";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

export function MobileBottomNav() {
  const t = useTranslations("mobileNav");
  const tNav = useTranslations("nav");
  const tAccount = useTranslations("account");
  const tAuth = useTranslations("auth");
  const { openSettings } = useSettingsDialog();
  const { user, logout } = useAuth();
  const { promptLogin } = useLoginPrompt();
  const [moreOpen, setMoreOpen] = useState(false);
  const { modules, activeModule, adminVisible } = useNavModules();
  const { primary, overflow } = splitMobileNav(modules);

  return (
    <>
      <nav
        className="border-border/70 bg-background/95 pb-safe fixed inset-x-0 bottom-0 z-50 border-t backdrop-blur md:hidden"
        aria-label={t("label")}
      >
        <div className="grid h-14 grid-cols-4">
          {primary.map((module) => {
            const active = activeModule?.key === module.key;
            const Icon = module.icon;
            return (
              <Link
                key={module.key}
                href={module.href}
                className={cn(
                  "flex min-h-11 flex-col items-center justify-center gap-0.5 px-1 text-[10px] transition-colors",
                  active ? "text-primary" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="size-5 shrink-0" />
                <span className="truncate">{tNav(module.key)}</span>
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
            {overflow.map((module) => {
              const Icon = module.icon;
              return (
                <Button key={module.key} variant="outline" className="h-11 justify-start" asChild>
                  <Link href={module.href} onClick={() => setMoreOpen(false)}>
                    <Icon className="size-4" />
                    {tNav(module.key)}
                  </Link>
                </Button>
              );
            })}
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
                openSettings("inference-setting");
              }}
            >
              <IconSettings className="size-4" />
              {tAccount("settings")}
            </Button>
            {adminVisible && (
              <Button variant="outline" className="h-11 justify-start" asChild>
                <Link href="/admin" onClick={() => setMoreOpen(false)}>
                  <IconAdmin className="size-4" />
                  {tAccount("adminPanel")}
                </Link>
              </Button>
            )}
            {user ? (
              <Button
                variant="outline"
                className="h-11 justify-start"
                onClick={() => {
                  setMoreOpen(false);
                  void logout();
                }}
              >
                <LogOut className="size-4" />
                {tAuth("logout")}
              </Button>
            ) : (
              <Button
                variant="outline"
                className="h-11 justify-start"
                onClick={() => {
                  setMoreOpen(false);
                  promptLogin();
                }}
              >
                <LogIn className="size-4" />
                {tAuth("loginRegister")}
              </Button>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
