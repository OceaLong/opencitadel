"use client";
import { type ReactNode, useState, useSyncExternalStore } from "react";

import { SidebarProvider } from "@/components/ui/sidebar";

import { type ClientDataScope, clientDataScopeKey } from "@/lib/data/client-data-scope";
import { readLayout, saveLayout } from "@/lib/execution-view/layout-preferences";
const subscribe = (notify: () => void) => {
  window.addEventListener("resize", notify);
  return () => window.removeEventListener("resize", notify);
};
/** Reuses the existing sidebar control; only layout preferences enter local storage. */
export function WorkbenchSidebar({
  scope,
  children,
}: {
  scope: ClientDataScope | null;
  children: ReactNode;
}) {
  const desktop = useSyncExternalStore(
    subscribe,
    () => window.innerWidth >= 1280,
    () => true,
  );
  const key = scope ? clientDataScopeKey(scope) : "";
  const [choice, setChoice] = useState<{ key: string; open: boolean } | null>(null);
  const saved = scope ? readLayout(scope).contextCollapsed : undefined;
  const open = choice?.key === key ? choice.open : saved === undefined ? desktop : !saved;
  return (
    <SidebarProvider
      open={open}
      onOpenChange={(next) => {
        setChoice({ key, open: next });
        if (scope) saveLayout(scope, { ...readLayout(scope), contextCollapsed: !next });
      }}
      className="[--sidebar-width:18rem] md:[--sidebar-left-offset:3.5rem] md:[--sidebar-width:280px]"
    >
      {children}
    </SidebarProvider>
  );
}
