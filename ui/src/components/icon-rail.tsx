"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { RailAccountMenu } from "@/components/rail-account-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";

import { useNavModules } from "@/hooks/use-nav-modules";
import { IconSettings } from "@/lib/icons";
import { ADMIN_NAV, type NavModule } from "@/lib/nav-modules";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

function RailTooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function RailModuleLink({ module, active }: { module: NavModule; active: boolean }) {
  const t = useTranslations("nav");
  const Icon = module.icon;

  return (
    <RailTooltip label={t(module.key)}>
      <Link
        href={module.href}
        aria-label={t(module.key)}
        aria-current={active ? "page" : undefined}
        className={cn(
          "relative flex size-10 items-center justify-center rounded-lg transition-colors",
          active
            ? "bg-muted/70 text-foreground"
            : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
        )}
      >
        {active ? (
          <span className="bg-primary absolute inset-y-2 -left-2 w-0.5 rounded-full" />
        ) : null}
        <Icon className="size-5" />
      </Link>
    </RailTooltip>
  );
}

export function IconRail() {
  const t = useTranslations("nav");
  const tSettings = useTranslations("settings");
  const tWorkspace = useTranslations("workspace");
  const tMeta = useTranslations("metadata");
  const { modules, activeModule, adminVisible } = useNavModules();
  const { user } = useAuth();
  const { openSettings } = useSettingsDialog();

  const logoButton = (
    <button
      type="button"
      className="hover:bg-muted/60 flex size-10 items-center justify-center rounded-lg transition-colors"
      aria-label={tWorkspace("label")}
    >
      <OpenCitadelIcon variant="icon" />
    </button>
  );

  return (
    <aside className="border-border/70 bg-sidebar hidden w-14 shrink-0 flex-col items-center gap-1 border-r py-2 md:flex">
      <div className="mb-2">
        {user ? (
          <WorkspaceSwitcher trigger={logoButton} />
        ) : (
          <Link
            href="/"
            aria-label={tMeta("title")}
            className="hover:bg-muted/60 flex size-10 items-center justify-center rounded-lg transition-colors"
          >
            <OpenCitadelIcon variant="icon" />
          </Link>
        )}
      </div>
      <nav aria-label={t("label")} className="flex flex-1 flex-col items-center gap-1">
        {modules.map((module) => (
          <RailModuleLink
            key={module.key}
            module={module}
            active={activeModule?.key === module.key}
          />
        ))}
      </nav>
      <div className="flex flex-col items-center gap-2 pb-1">
        {adminVisible ? (
          <RailModuleLink module={ADMIN_NAV} active={activeModule?.key === "admin"} />
        ) : null}
        <RailTooltip label={tSettings("inference")}>
          <button
            type="button"
            className="text-muted-foreground hover:bg-muted/50 hover:text-foreground flex size-9 items-center justify-center rounded-lg transition-colors"
            aria-label={tSettings("openInferenceLabel")}
            onClick={() => openSettings("inference-setting")}
          >
            <IconSettings className="size-4" />
          </button>
        </RailTooltip>
        <RailAccountMenu />
      </div>
    </aside>
  );
}
