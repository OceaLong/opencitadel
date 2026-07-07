"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { CSSProperties } from "react";

import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { NotificationInbox } from "@/components/notification-inbox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";

import { isModelUnavailableStatus, llmStatusApi } from "@/lib/api/llm-status";
import type { LLMStatusData } from "@/lib/api/types";
import {
  IconAutomation,
  IconCodebase,
  IconKnowledge,
  IconMarketplace,
  IconSettings,
  IconWorkspace,
} from "@/lib/icons";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";
import { cn } from "@/lib/utils";

function AppHeaderSidebarTrigger() {
  const { open, isMobile } = useSidebar();

  if (open && !isMobile) return null;

  return <SidebarTrigger className="cursor-pointer" />;
}

export function AppHeader() {
  const t = useTranslations("chatHeader");
  const tSettings = useTranslations("settings");
  const tMeta = useTranslations("metadata");
  const { openSettings } = useSettingsDialog();
  const [llmStatus, setLlmStatus] = useState<LLMStatusData["status"]>("unknown");

  const modelStatusKey =
    llmStatus === "unknown"
      ? "unknown"
      : isModelUnavailableStatus(llmStatus)
        ? "unavailable"
        : "ok";

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const data = await llmStatusApi.getStatus();
        if (mounted) setLlmStatus(data.status ?? "unknown");
      } catch {
        if (mounted) setLlmStatus("unknown");
      }
    };
    void load();
    const timer = setInterval(load, isModelUnavailableStatus(llmStatus) ? 10_000 : 30_000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [llmStatus]);

  return (
    <header className="border-border/70 bg-background/95 z-50 flex w-full shrink-0 items-center justify-between border-b px-4 py-2 backdrop-blur">
      <div className="flex items-center gap-2">
        <AppHeaderSidebarTrigger />
        <Link
          href="/"
          className="border-border/60 bg-card text-foreground hover:bg-muted/60 flex h-9 items-center gap-2 rounded-xl border px-3 shadow-card transition-colors"
          style={{ "--logo-color": "currentColor" } as CSSProperties}
          aria-label={t("backHome")}
        >
          <OpenCitadelIcon />
          <span className="sr-only">{tMeta("title")}</span>
        </Link>
      </div>
      <div className="flex items-center gap-1">
        <Badge
          variant={isModelUnavailableStatus(llmStatus) ? "destructive" : "secondary"}
          className="text-2xs hidden sm:inline-flex"
        >
          {t("modelStatus", { status: modelStatusKey })}
        </Badge>
        <span
          className={cn(
            "inline-flex size-2.5 shrink-0 rounded-full sm:hidden",
            isModelUnavailableStatus(llmStatus) ? "bg-destructive" : "bg-success",
            llmStatus === "unknown" && "bg-muted-foreground",
          )}
          title={t("modelStatus", { status: modelStatusKey })}
          aria-label={t("modelStatus", { status: modelStatusKey })}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={t("workspaceMenu")}
              title={t("workspaceMenu")}
              className="hidden md:inline-flex"
            >
              <IconWorkspace className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuLabel>{t("workspaceMenu")}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/codebase" className="cursor-pointer">
                <IconCodebase className="size-4" />
                {t("codebase")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/knowledge" className="cursor-pointer">
                <IconKnowledge className="size-4" />
                {t("knowledge")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/marketplace" className="cursor-pointer">
                <IconMarketplace className="size-4" />
                {t("marketplace")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/automation" className="cursor-pointer">
                <IconAutomation className="size-4" />
                {t("automation")}
              </Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <NotificationInbox />
        <Button
          variant="outline"
          size="icon-sm"
          className="cursor-pointer"
          aria-label={tSettings("openModelsLabel")}
          title={tSettings("models")}
          onClick={() => openSettings("models-setting")}
        >
          <IconSettings className="size-4" />
        </Button>
      </div>
    </header>
  );
}
