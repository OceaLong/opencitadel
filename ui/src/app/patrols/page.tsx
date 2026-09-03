"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus, RefreshCw } from "lucide-react";

import { AsyncBoundary } from "@/components/async-boundary";
import { PageHeader } from "@/components/page-header";
import { PatrolPackList } from "@/components/patrol/patrol-pack-list";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { Button } from "@/components/ui/button";

import { useCapabilities } from "@/hooks/use-capabilities";
import { usePatrolPacks } from "@/hooks/use-patrol-packs";
import { isCapabilityAvailable } from "@/lib/api/capabilities";
import { useAuth } from "@/providers/auth-provider";

export default function PatrolsPage() {
  const t = useTranslations("patrol");
  const tNav = useTranslations("nav");
  const { loading: capabilityLoading, capability } = useCapabilities();
  const runAdmissionAvailable = isCapabilityAvailable(capability("ops_patrol"));
  const { user, loading: authLoading } = useAuth();
  const readOnly = user?.global_role === "auditor";
  const { packs, latestRuns, loading, error, refresh, trigger, triggeringId, toggle, actionId } =
    usePatrolPacks();
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
        <AsyncBoundary
          loading={false}
          error={loading ? null : error}
          onRetry={() => void refresh()}
        >
          <PatrolPackList
            packs={packs}
            latestRuns={latestRuns}
            loading={loading}
            onTrigger={(pack) => void trigger(pack.id)}
            onToggle={(pack) => void toggle(pack)}
            triggeringId={triggeringId}
            actionId={actionId}
            readOnly={readOnly}
            runAdmissionDisabled={capabilityLoading || !runAdmissionAvailable}
          />
        </AsyncBoundary>
      </div>
    </ScrollablePageContent>
  );
}
