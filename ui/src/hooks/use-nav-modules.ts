"use client";

import { usePathname } from "next/navigation";

import { useCapabilities } from "@/hooks/use-capabilities";
import { hasExecutionGrant } from "@/lib/api/capabilities";
import { matchModule, NAV_MODULES, type NavModule } from "@/lib/nav-modules";
import { useAuth } from "@/providers/auth-provider";

export function useNavModules(): {
  modules: NavModule[];
  activeModule: NavModule | undefined;
  adminVisible: boolean;
} {
  const pathname = usePathname();
  const { user } = useAuth();
  const { snapshot } = useCapabilities();

  const adminVisible = user?.global_role === "admin" || user?.global_role === "auditor";

  return {
    modules: NAV_MODULES.filter(
      (module) =>
        (module.key !== "evaluations" ||
          hasExecutionGrant(snapshot ?? undefined, "evaluation.read")) &&
        (module.key !== "analysis" || hasExecutionGrant(snapshot ?? undefined, "execution.read")),
    ),
    activeModule: matchModule(pathname),
    adminVisible,
  };
}
