"use client";

import { Fragment } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { NotificationInbox } from "@/components/notification-inbox";
import { Badge } from "@/components/ui/badge";
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
  const { capability } = useCapabilities();
  const chatState = capability("chat")?.state;
  const modelUnavailable = Boolean(chatState && chatState !== "available");

  const modelStatusKey =
    chatState === undefined ? "unknown" : modelUnavailable ? "unavailable" : "ok";

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
        <Badge
          variant={modelUnavailable ? "destructive" : "secondary"}
          className="text-2xs hidden sm:inline-flex"
        >
          {t("modelStatus", { status: modelStatusKey })}
        </Badge>
        <span
          className={cn(
            "inline-flex size-2.5 shrink-0 rounded-full sm:hidden",
            modelUnavailable ? "bg-destructive" : "bg-success",
            chatState === undefined && "bg-muted-foreground",
          )}
          title={t("modelStatus", { status: modelStatusKey })}
          aria-label={t("modelStatus", { status: modelStatusKey })}
        />
        <NotificationInbox />
      </div>
    </header>
  );
}
