import type { LucideIcon } from "lucide-react";

import { IconIntegration, IconMemory, IconModel, IconSkill } from "@/lib/icons";

export type SettingsNavItem = {
  href: string;
  labelKey: "models" | "skills" | "memory" | "integrations";
  icon: LucideIcon;
};

export const SETTINGS_NAV: SettingsNavItem[] = [
  { href: "/settings/models", labelKey: "models", icon: IconModel },
  { href: "/settings/skills", labelKey: "skills", icon: IconSkill },
  { href: "/settings/memory", labelKey: "memory", icon: IconMemory },
  { href: "/settings/integrations", labelKey: "integrations", icon: IconIntegration },
];
