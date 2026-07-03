"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import { AppHeader } from "@/components/app-header";
import { LeftPanel } from "@/components/left-panel";
import { SidebarProvider } from "@/components/ui/sidebar";

import { useAuth } from "@/providers/auth-provider";
import { LoginPromptProvider } from "@/providers/login-prompt-provider";
import { SettingsDialogProvider } from "@/providers/settings-dialog-provider";
import { SessionsProvider } from "@/providers/sessions-provider";

type SidebarLayoutStyle = React.CSSProperties & {
  "--sidebar-width": string;
  "--sidebar-width-icon": string;
};

const AUTH_PREFIXES = ["/login", "/register"];
const SHELLLESS_PREFIXES = ["/share/artifact", "/admin", "/invitations"];
const AUTH_REQUIRED_PREFIXES = ["/admin", "/teams"];

function isAuthRoute(pathname: string): boolean {
  return AUTH_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isShelllessRoute(pathname: string): boolean {
  return (
    isAuthRoute(pathname) ||
    pathname === "/admin" ||
    pathname.startsWith("/admin/") ||
    SHELLLESS_PREFIXES.some((prefix) => pathname.startsWith(prefix))
  );
}

function requiresAuth(pathname: string): boolean {
  return AUTH_REQUIRED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const tCommon = useTranslations("common");
  const shelllessRoute = isShelllessRoute(pathname);
  const authRequiredRoute = requiresAuth(pathname);

  useEffect(() => {
    if (!loading && !user && authRequiredRoute) {
      router.replace("/login");
    }
  }, [loading, authRequiredRoute, router, user]);

  if (loading && authRequiredRoute) {
    return <div className="bg-background flex min-h-screen items-center justify-center text-sm">{tCommon("loading")}</div>;
  }

  if (!user && authRequiredRoute) {
    return null;
  }

  const sidebarStyle: SidebarLayoutStyle = {
    "--sidebar-width": "300px",
    "--sidebar-width-icon": "300px",
  };

  const content = shelllessRoute ? (
    <div className="bg-background min-h-screen">{children}</div>
  ) : (
    <SessionsProvider>
      <SidebarProvider style={sidebarStyle}>
        <LeftPanel />
        <div className="bg-background flex h-screen min-w-0 flex-1 flex-col overflow-hidden">
          <AppHeader />
          <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
        </div>
      </SidebarProvider>
    </SessionsProvider>
  );

  return (
    <SettingsDialogProvider>
      <LoginPromptProvider>{content}</LoginPromptProvider>
    </SettingsDialogProvider>
  );
}
