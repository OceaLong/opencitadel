"use client";

import { usePathname } from "next/navigation";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
import { matchModule, NAV_MODULES, type NavModule } from "@/lib/nav-modules";
import { useAuth } from "@/providers/auth-provider";

export function useNavModules(): {
  modules: NavModule[];
  activeModule: NavModule | undefined;
  adminVisible: boolean;
} {
  const pathname = usePathname();
  const flags = useFeatureFlags();
  const { user } = useAuth();

  const modules = NAV_MODULES.filter(
    (module) => !module.requiresFlag || flags[module.requiresFlag],
  );
  const adminVisible =
    user?.global_role === "admin" || user?.global_role === "auditor";

  return { modules, activeModule: matchModule(pathname), adminVisible };
}
