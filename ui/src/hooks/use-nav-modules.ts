"use client";

import { usePathname } from "next/navigation";

import { matchModule, NAV_MODULES, type NavModule } from "@/lib/nav-modules";
import { useAuth } from "@/providers/auth-provider";

export function useNavModules(): {
  modules: NavModule[];
  activeModule: NavModule | undefined;
  adminVisible: boolean;
} {
  const pathname = usePathname();
  const { user } = useAuth();

  const adminVisible = user?.global_role === "admin" || user?.global_role === "auditor";

  return { modules: NAV_MODULES, activeModule: matchModule(pathname), adminVisible };
}
