"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { AdminContextPanel } from "@/components/admin/admin-context-panel";
import { AppHeader } from "@/components/app-header";
import { IconRail } from "@/components/icon-rail";
import { LeftPanel } from "@/components/left-panel";
import { MobileBottomNav } from "@/components/mobile-bottom-nav";
import { PatrolContextPanel } from "@/components/patrol/patrol-context-panel";
import { SidebarProvider } from "@/components/ui/sidebar";

import { matchModule, type NavModule } from "@/lib/nav-modules";
import { useAuth } from "@/providers/auth-provider";
import { LoginPromptProvider } from "@/providers/login-prompt-provider";
import { PageTitleProvider } from "@/providers/page-title-provider";
import { PatrolPacksProvider } from "@/providers/patrol-packs-provider";
import { SessionsProvider } from "@/providers/sessions-provider";
import { SettingsDialogProvider } from "@/providers/settings-dialog-provider";

const AUTH_PREFIXES = ["/login", "/register"];
const SHELLLESS_PREFIXES = ["/share/artifact", "/invitations"];
const AUTH_REQUIRED_PREFIXES = ["/admin", "/teams"];

function isAuthRoute(pathname: string): boolean {
  return AUTH_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isShelllessRoute(pathname: string): boolean {
  return isAuthRoute(pathname) || SHELLLESS_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function requiresAuth(pathname: string): boolean {
  return AUTH_REQUIRED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function ContextPanel({ module }: { module: NavModule | undefined }) {
  if (!module?.hasContextPanel) return null;
  if (module.key === "chat")
    return (
      <SessionsProvider>
        <LeftPanel />
      </SessionsProvider>
    );
  if (module.key === "admin") return <AdminContextPanel />;
  if (module.key === "patrol") return <PatrolContextPanel />;
  return null;
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const tCommon = useTranslations("common");
  const shelllessRoute = isShelllessRoute(pathname);
  const authRequiredRoute = requiresAuth(pathname);
  const activeModule = matchModule(pathname);

  useEffect(() => {
    if (!loading && !user && authRequiredRoute) {
      router.replace("/login");
    }
  }, [loading, authRequiredRoute, router, user]);

  if (loading && authRequiredRoute) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center text-sm">
        {tCommon("loading")}
      </div>
    );
  }

  if (!user && authRequiredRoute) {
    return null;
  }

  const shellBody = (
    <>
      <ContextPanel module={activeModule} />
      <div className="bg-background flex h-screen min-w-0 flex-1 flex-col overflow-hidden">
        <AppHeader />
        <div className="pb-mobile-nav min-h-0 flex-1 overflow-hidden md:pb-0">{children}</div>
        <MobileBottomNav />
      </div>
    </>
  );

  const content = shelllessRoute ? (
    <div className="bg-background min-h-screen">{children}</div>
  ) : (
    <PageTitleProvider>
      <SidebarProvider className="[--sidebar-width:18rem] md:[--sidebar-left-offset:3.5rem] md:[--sidebar-width:280px]">
        <IconRail />
        {activeModule?.key === "patrol" ? (
          <PatrolPacksProvider>{shellBody}</PatrolPacksProvider>
        ) : (
          shellBody
        )}
      </SidebarProvider>
    </PageTitleProvider>
  );

  return (
    <SettingsDialogProvider>
      <LoginPromptProvider>{content}</LoginPromptProvider>
    </SettingsDialogProvider>
  );
}
