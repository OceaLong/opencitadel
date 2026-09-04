"use client";

import { Fragment } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { ApprovalsIndicator } from "@/components/approvals-indicator";
import { NotificationInbox } from "@/components/notification-inbox";
import { StatusBadge } from "@/components/status-badge";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";

import { useCapabilities } from "@/hooks/use-capabilities";
import { useNavModules } from "@/hooks/use-nav-modules";
import { matchAdminNav } from "@/lib/admin-nav";
import { cn } from "@/lib/utils";
import { usePageTitle } from "@/providers/page-title-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

function AppHeaderSidebarTrigger() {
  const { open, isMobile } = useSidebar();

  if (open && !isMobile) return null;

  return <SidebarTrigger className="cursor-pointer" />;
}

export function AppHeader() {
  const t = useTranslations("chatHeader");
  const tNav = useTranslations("nav");
  const tAdminNav = useTranslations("adminNav");
  const pathname = usePathname();
  const { activeModule, adminVisible, modules } = useNavModules();
  const moduleVisible =
    !!activeModule &&
    (modules.some((module) => module.key === activeModule.key) || activeModule.key === "admin");
  const showActiveModule = moduleVisible && (activeModule?.key !== "admin" || adminVisible);
  const pageTitle = usePageTitle();
  const adminItem = activeModule?.key === "admin" ? matchAdminNav(pathname) : undefined;
  const { capability, loading: capabilitiesLoading } = useCapabilities();
  const { openSettings } = useSettingsDialog();
  const chatState = capability("chat")?.state;
  const modelUnavailable = Boolean(chatState && chatState !== "available");

  const modelStatusKey =
    chatState === undefined ? "unknown" : modelUnavailable ? "unavailable" : "ok";
  const modelStatusLabel = t("modelStatus", { status: modelStatusKey });
  const modelStatusTitle = `${modelStatusLabel} · ${t("modelStatusTooltip")}`;
  // unknown 且加载中：中性灰点 + 呼吸动画，绝不闪红。
  const modelStatusDotClass =
    modelStatusKey === "unavailable"
      ? "bg-destructive"
      : modelStatusKey === "ok"
        ? "bg-success"
        : cn("bg-muted-foreground", capabilitiesLoading && "animate-pulse");

  const crumbs: { label: string; href?: string }[] = [];
  if (activeModule && showActiveModule) {
    crumbs.push({ label: tNav(activeModule.key), href: activeModule.href });
    if (adminItem && adminItem.href !== "/admin") {
      crumbs.push({ label: tAdminNav(adminItem.labelKey), href: adminItem.href });
    }
    if (pageTitle) crumbs.push({ label: pageTitle });
  }

  return (
    <header className="border-border/70 bg-background/95 z-50 flex h-12 w-full shrink-0 items-center justify-between border-b px-4 backdrop-blur">
      <div className="flex min-w-0 items-center gap-2">
        {activeModule?.hasContextPanel && showActiveModule ? <AppHeaderSidebarTrigger /> : null}
        {crumbs.length <= 1 ? (
          <span className="truncate text-sm font-medium">{crumbs[0]?.label ?? null}</span>
        ) : (
          <Breadcrumb className="min-w-0">
            <BreadcrumbList className="flex-nowrap">
              {crumbs.map((crumb, index) => {
                const isLast = index === crumbs.length - 1;
                return (
                  <Fragment key={`${crumb.label}-${index}`}>
                    <BreadcrumbItem className="min-w-0">
                      {isLast || !crumb.href ? (
                        <BreadcrumbPage className="truncate text-sm font-medium">
                          {crumb.label}
                        </BreadcrumbPage>
                      ) : (
                        <BreadcrumbLink asChild className="truncate">
                          <Link href={crumb.href}>{crumb.label}</Link>
                        </BreadcrumbLink>
                      )}
                    </BreadcrumbItem>
                    {isLast ? null : <BreadcrumbSeparator />}
                  </Fragment>
                );
              })}
            </BreadcrumbList>
          </Breadcrumb>
        )}
      </div>
      <div className="flex items-center gap-1">
        {/* 模型状态芯片：圆点 + 文案，点击直达推理设置（模型不可用时的修复入口）。 */}
        <button
          type="button"
          className="inline-flex cursor-pointer items-center"
          onClick={() => openSettings("inference-setting")}
          title={modelStatusTitle}
          aria-label={modelStatusTitle}
        >
          <StatusBadge
            variant={modelUnavailable ? "destructive" : "secondary"}
            className="hidden gap-1.5 sm:inline-flex"
          >
            <span
              className={cn("size-1.5 shrink-0 rounded-full", modelStatusDotClass)}
              aria-hidden
            />
            {modelStatusLabel}
          </StatusBadge>
          {/* 移动端（sm 以下）保留纯圆点 */}
          <span
            className={cn(
              "inline-flex size-2.5 shrink-0 rounded-full sm:hidden",
              modelStatusDotClass,
            )}
            aria-hidden
          />
        </button>
        <ApprovalsIndicator />
        <NotificationInbox />
      </div>
    </header>
  );
}
