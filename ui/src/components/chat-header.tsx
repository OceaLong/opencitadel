"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Settings } from "lucide-react";
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
import { ThemeToggle } from "@/components/theme-toggle";

import { isModelUnavailableStatus, llmStatusApi } from "@/lib/api/llm-status";
import type { LLMStatusData } from "@/lib/api/types";
import {
  IconAutomation,
  IconCodebase,
  IconKnowledge,
  IconMarketplace,
  IconWorkspace,
} from "@/lib/icons";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

function ChatHeaderSidebarTrigger() {
  const { open, isMobile } = useSidebar();

  if (open && !isMobile) return null;

  return <SidebarTrigger className="cursor-pointer" />;
}

export function ChatHeader() {
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
    <header className="z-50 flex w-full items-center justify-between px-4 py-2">
      <div className="flex items-center gap-2">
        <ChatHeaderSidebarTrigger />
        <Link
          href="/"
          className="border-border/60 bg-card text-foreground hover:bg-muted/60 flex h-9 items-center gap-2 rounded-xl border px-3 shadow-[var(--shadow-card)] transition-colors"
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
          className="hidden text-[10px] sm:inline-flex"
        >
          {t("modelStatus", { status: modelStatusKey })}
        </Badge>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={t("workspaceMenu")}
              title={t("workspaceMenu")}
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
        <ThemeToggle />
        <Button
          variant="outline"
          size="icon-sm"
          className="cursor-pointer"
          aria-label={tSettings("openLabel")}
          title={tSettings("settingsTitle")}
          onClick={() => openSettings()}
        >
          <Settings className="size-4" />
        </Button>
      </div>
    </header>
  );
}
