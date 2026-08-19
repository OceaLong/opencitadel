"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { StatusBadge } from "@/components/status-badge";
import { Sidebar, SidebarContent, SidebarHeader, SidebarTrigger } from "@/components/ui/sidebar";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
import { patrolStatusVariant, usePatrolLabels } from "@/hooks/use-patrol-labels";
import { usePatrolPacks } from "@/hooks/use-patrol-packs";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

export function PatrolContextPanel() {
  const pathname = usePathname();
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  const { user } = useAuth();
  const readOnly = user?.global_role === "auditor";
  const { loading: flagLoading, opsPatrolEnabled } = useFeatureFlags();
  const { packs, latestRuns, loading } = usePatrolPacks();

  if (flagLoading || !opsPatrolEnabled) return null;

  return (
    <Sidebar>
      <SidebarHeader>
        <SidebarTrigger className="cursor-pointer" />
      </SidebarHeader>
      <SidebarContent className="p-2">
        {loading ? null : (
          <div className="space-y-1">
            {!readOnly && (
              <Link
                href="/patrols/new"
                className="text-muted-foreground hover:bg-muted/60 hover:text-foreground flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors"
              >
                <Plus className="size-4" />
                {t("actions.create")}
              </Link>
            )}
            <nav className="space-y-1">
              {packs.map((pack) => {
                const run = latestRuns[pack.id];
                const active = pathname === `/patrols/${pack.id}`;
                return (
                  <Link
                    key={pack.id}
                    href={`/patrols/${pack.id}`}
                    className={cn(
                      "flex min-h-10 flex-col gap-1 rounded-lg px-3 py-2 text-sm transition-colors",
                      active
                        ? "bg-muted text-foreground font-medium"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 truncate">{pack.name}</span>
                      <StatusBadge variant={patrolStatusVariant(pack.status)}>
                        {labels.status[pack.status] ?? pack.status}
                      </StatusBadge>
                    </span>
                    {run ? (
                      <span className="text-muted-foreground font-mono text-xs">
                        PASS {run.counts.pass} · FAIL {run.counts.fail}
                      </span>
                    ) : null}
                  </Link>
                );
              })}
            </nav>
          </div>
        )}
      </SidebarContent>
      <div className="md:hidden">
        <AccountMenu />
      </div>
    </Sidebar>
  );
}
