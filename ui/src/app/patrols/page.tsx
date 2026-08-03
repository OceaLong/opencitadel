"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { PatrolPackList } from "@/components/patrol/patrol-pack-list";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolPack, PatrolRun } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";

export default function PatrolsPage() {
  const t = useTranslations("patrol");
  const { loading: flagLoading, opsPatrolEnabled } = useFeatureFlags();
  const { user, loading: authLoading } = useAuth();
  const readOnly = user?.global_role === "auditor";
  const [packs, setPacks] = useState<PatrolPack[]>([]);
  const [runs, setRuns] = useState<PatrolRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [triggeringId, setTriggeringId] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [packData, runData] = await Promise.all([
        patrolsApi.listPacks(),
        patrolsApi.listRuns({ limit: 100 }),
      ]);
      setPacks(packData.items);
      setRuns(runData.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.load"));
    } finally {
      setLoading(false);
    }
  }, [t]);
  useEffect(() => {
    if (opsPatrolEnabled) void load();
  }, [load, opsPatrolEnabled]);
  const latestRuns = useMemo(
    () => Object.fromEntries(runs.map((run) => [run.pack_id, run]).reverse()),
    [runs],
  );
  const trigger = async (pack: PatrolPack) => {
    setTriggeringId(pack.id);
    try {
      const run = await patrolsApi.triggerPack(
        pack.id,
        globalThis.crypto?.randomUUID?.() ?? `${pack.id}-${Date.now()}`,
      );
      toast.success(t("toast.started"));
      window.location.href = `/patrol-runs/${run.id}`;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.trigger"));
    } finally {
      setTriggeringId(null);
    }
  };
  const toggle = async (pack: PatrolPack) => {
    setActionId(pack.id);
    try {
      if (pack.status === "active") await patrolsApi.pausePack(pack.id);
      else await patrolsApi.activatePack(pack.id);
      toast.success(pack.status === "active" ? t("toast.paused") : t("toast.activated"));
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.action"));
    } finally {
      setActionId(null);
    }
  };
  if (!flagLoading && !opsPatrolEnabled)
    return (
      <ScrollablePageContent>
        <Card>
          <CardContent className="p-8 text-center">
            <h1 className="font-semibold">{t("disabled.title")}</h1>
            <p className="text-muted-foreground mt-2 text-sm">{t("disabled.description")}</p>
          </CardContent>
        </Card>
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <div className="grid gap-5 p-4 sm:p-6">
        <PageHeader
          bordered={false}
          title="Ops Patrol"
          description={t("description")}
          actions={
            <>
              <Button variant="outline" onClick={() => void load()}>
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
          onTrigger={(pack) => void trigger(pack)}
          onToggle={(pack) => void toggle(pack)}
          triggeringId={triggeringId}
          actionId={actionId}
          readOnly={readOnly}
        />
      </div>
    </ScrollablePageContent>
  );
}
