"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus, RefreshCw } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PatrolPackList } from "@/components/patrol/patrol-pack-list";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { Button } from "@/components/ui/button";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
import { usePatrolPacks } from "@/hooks/use-patrol-packs";
import { useAuth } from "@/providers/auth-provider";

export default function PatrolsPage() {
  const t = useTranslations("patrol");
  const tNav = useTranslations("nav");
  const { loading: flagLoading, opsPatrolEnabled } = useFeatureFlags();
  const { user, loading: authLoading } = useAuth();
  const readOnly = user?.global_role === "auditor";
  const { packs, latestRuns, loading, refresh, trigger, triggeringId, toggle, actionId } =
    usePatrolPacks();
  if (!flagLoading && !opsPatrolEnabled)
    return (
      <ScrollablePageContent>
        <EmptyState
          variant="dashed"
          title={t("disabled.title")}
          description={t("disabled.description")}
        />
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <div className="grid gap-5">
        <PageHeader
          title={tNav("patrol")}
          description={t("description")}
          actions={
            <>
              <Button variant="outline" onClick={() => void refresh()}>
                <RefreshCw className="size-4" />
                {t("actions.refresh")}
              </Button>
              {!readOnly && !authLoading && (
                <Button asChild>
                  <Link href="/patrols/new">
                    <Plus className="size-4" />
                    {t("actions.create")}
                  </Link>
                </Button>
              )}
            </>
          }
        />
        <PatrolPackList
          packs={packs}
          latestRuns={latestRuns}
          loading={loading || flagLoading}
          onTrigger={(pack) => void trigger(pack.id)}
          onToggle={(pack) => void toggle(pack)}
          triggeringId={triggeringId}
          actionId={actionId}
          readOnly={readOnly}
        />
      </div>
    </ScrollablePageContent>
  );
}
