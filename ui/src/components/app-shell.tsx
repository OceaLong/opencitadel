"use client";

import { usePathname } from "next/navigation";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { LeftPanel } from "@/components/left-panel";
import { SidebarProvider } from "@/components/ui/sidebar";

import { SessionsProvider } from "@/providers/sessions-provider";
import { useAuth } from "@/providers/auth-provider";

type SidebarLayoutStyle = React.CSSProperties & {
  "--sidebar-width": string;
  "--sidebar-width-icon": string;
};

const SHELLLESS_PREFIXES = ["/q/", "/room/", "/share/"];
const PUBLIC_PREFIXES = ["/q/", "/room/", "/share/", "/login", "/register"];

function isShelllessRoute(pathname: string): boolean {
  return (
    pathname === "/codebase" ||
    pathname.startsWith("/codebase/") ||
    pathname === "/knowledge" ||
    pathname.startsWith("/knowledge/") ||
    SHELLLESS_PREFIXES.some((prefix) => pathname.startsWith(prefix))
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const shelllessRoute = isShelllessRoute(pathname);
  const publicRoute = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));

  useEffect(() => {
    if (!loading && !user && !publicRoute) {
      router.replace("/login");
    }
  }, [loading, publicRoute, router, user]);

  if (loading && !publicRoute) {
    return <div className="bg-background flex min-h-screen items-center justify-center text-sm">加载中...</div>;
  }

  if (!user && !publicRoute) {
    return null;
  }

  const sidebarStyle: SidebarLayoutStyle = {
    "--sidebar-width": "300px",
    "--sidebar-width-icon": "300px",
  };

  if (shelllessRoute) {
    return <div className="bg-background min-h-screen">{children}</div>;
  }

  return (
    <SessionsProvider>
      <SidebarProvider style={sidebarStyle}>
        <LeftPanel />
        <div className="bg-background h-screen flex-1 overflow-hidden">{children}</div>
      </SidebarProvider>
    </SessionsProvider>
  );
}
