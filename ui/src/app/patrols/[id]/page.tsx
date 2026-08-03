"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Loader2, Pause, Play, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
import { usePatrolLabels } from "@/hooks/use-patrol-labels";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolPack, PatrolPackMetrics, PatrolRun } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";

export default function PatrolPackPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  const { opsPatrolEnabled, loading: flagLoading } = useFeatureFlags();
  const { user } = useAuth();
  const readOnly = user?.global_role === "auditor";
  const [pack, setPack] = useState<PatrolPack | null>(null);
  const [runs, setRuns] = useState<PatrolRun[]>([]);
  const [metrics, setMetrics] = useState<PatrolPackMetrics | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    const [item, history, metricData] = await Promise.all([
      patrolsApi.getPack(id),
      patrolsApi.listRuns({ pack_id: id, limit: 20 }),
      patrolsApi.getPackMetrics(id),
    ]);
    setPack(item);
    setRuns(history.items);
    setMetrics(metricData);
  }, [id]);
  useEffect(() => {
    if (opsPatrolEnabled)
      void load().catch((error) =>
        toast.error(error instanceof Error ? error.message : t("errors.load")),
      );
  }, [load, opsPatrolEnabled, t]);
  const action = async (name: "validate" | "activate" | "pause" | "trigger") => {
    if (!pack) return;
    setBusy(name);
    try {
      if (name === "validate") setPack(await patrolsApi.validatePack(pack.id));
      if (name === "activate") setPack(await patrolsApi.activatePack(pack.id));
      if (name === "pause") setPack(await patrolsApi.pausePack(pack.id));
      if (name === "trigger") {
        const run = await patrolsApi.triggerPack(
          pack.id,
          globalThis.crypto?.randomUUID?.() ?? `${pack.id}-${Date.now()}`,
        );
        window.location.href = `/patrol-runs/${run.id}`;
      }
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.action"));
    } finally {
      setBusy(null);
    }
  };
  if (flagLoading || !pack)
    return (
      <ScrollablePageContent>
        <div className="grid gap-3 p-6">
          <Skeleton className="h-24" />
          <Skeleton className="h-64" />
        </div>
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <div className="grid gap-5 p-4 sm:p-6">
        <PageHeader
          bordered={false}
          title={
            <span className="flex flex-wrap items-center gap-2">
              {pack.name}
              <StatusBadge>{labels.status[pack.status] ?? pack.status}</StatusBadge>
            </span>
          }
          description={`Pack v${pack.version} · ${pack.config.target_ref}`}
          actions={
            !readOnly && (
              <>
                <Button
                  variant="outline"
                  disabled={Boolean(busy)}
                  onClick={() => void action("validate")}
                >
                  <ShieldCheck className="size-4" />
                  {t("actions.revalidate")}
                </Button>
                {pack.status === "active" ? (
                  <Button variant="outline" onClick={() => void action("pause")}>
                    <Pause className="size-4" />
                    {t("actions.pause")}
                  </Button>
                ) : (
                  <Button
                    disabled={pack.last_validated_version !== pack.version}
                    onClick={() => void action("activate")}
                  >
                    <Play className="size-4" />
                    {t("actions.activate")}
                  </Button>
                )}
                <Button
                  disabled={pack.status !== "active" || Boolean(busy)}
                  onClick={() => void action("trigger")}
                >
                  {busy === "trigger" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Play className="size-4" />
                  )}
                  {t("actions.runNow")}
                </Button>
              </>
            )
          }
        />
        {pack.last_validated_version !== pack.version && (
          <div className="border-warning/30 bg-warning/5 rounded-lg border p-3 text-sm">
            {t("pack.needsValidation")}
          </div>
        )}
        {pack.validation_summary.errors?.length ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("pack.validationErrors")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {pack.validation_summary.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : null}
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>{t("pack.targetSchedule")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm">
              <p>
                {t("pack.environment")}: {pack.config.scope.environment}
              </p>
              <p>Cluster: {pack.config.scope.cluster}</p>
              <p>Namespace: {pack.config.scope.namespaces.join(", ")}</p>
              <p>
                {t("pack.schedule")}: {pack.config.schedule.cron} · {pack.config.timezone}
              </p>
              <p>
                {t("pack.nextRun")}:{" "}
                {pack.next_run_at ? new Date(pack.next_run_at).toLocaleString() : t("scheduleOff")}
              </p>
              <p>Collector: {pack.mcp_server_id}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>{t("pack.capabilities")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm">
              <p>
                Capability hash:{" "}
                <code className="text-xs break-all">
                  {pack.validation_summary.capability_hash ?? t("pack.notValidated")}
                </code>
              </p>
              <p>
                {t("pack.verifiedTools", {
                  count: pack.validation_summary.enabled_tools?.length ?? 0,
                })}
              </p>
              <p>{t("pack.boundary")}</p>
            </CardContent>
          </Card>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>{t("metrics.title")}</CardTitle>
          </CardHeader>
          <CardContent>
            {!metrics || metrics.sample_size < 7 ? (
              <p className="text-muted-foreground text-sm">
                {t("metrics.insufficient", { count: metrics?.sample_size ?? 0 })}
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-lg border p-3">
                  <p className="text-muted-foreground text-xs">{t("metrics.successRate")}</p>
                  <p className="mt-1 text-2xl font-semibold">
                    {metrics.scheduled_success_rate !== null &&
                    metrics.scheduled_success_rate !== undefined
                      ? `${Math.round(metrics.scheduled_success_rate * 100)}%`
                      : "—"}
                  </p>
                </div>
                <div className="rounded-lg border p-3">
                  <p className="text-muted-foreground text-xs">{t("metrics.findings")}</p>
                  <p className="mt-1 text-2xl font-semibold">{metrics.finding_count}</p>
                </div>
                <div className="rounded-lg border p-3">
                  <p className="text-muted-foreground text-xs">{t("metrics.falsePositives")}</p>
                  <p className="mt-1 text-2xl font-semibold">{metrics.false_positive_count}</p>
                </div>
                <div className="rounded-lg border p-3">
                  <p className="text-muted-foreground text-xs">{t("metrics.reviewTime")}</p>
                  <p className="mt-1 text-2xl font-semibold">
                    {metrics.median_review_minutes === null ||
                    metrics.median_review_minutes === undefined
                      ? "—"
                      : t("metrics.minutes", {
                          value: metrics.median_review_minutes.toFixed(1),
                        })}
                  </p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t("pack.recentRuns")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2">
            {runs.length ? (
              runs.slice(0, 20).map((run) => (
                <Link
                  href={`/patrol-runs/${run.id}`}
                  key={run.id}
                  className="hover:bg-muted/50 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3"
                >
                  <span className="text-sm">
                    {new Date(run.created_at).toLocaleString()} · v{run.pack_version}
                  </span>
                  <span className="flex items-center gap-2">
                    <StatusBadge>{labels.status[run.status] ?? run.status}</StatusBadge>
                    <span className="text-muted-foreground text-xs">
                      PASS {run.counts.pass} / Finding{" "}
                      {run.counts.warn + run.counts.fail + run.counts.error}
                    </span>
                  </span>
                </Link>
              ))
            ) : (
              <p className="text-muted-foreground text-sm">{t("empty.noRuns")}</p>
            )}
          </CardContent>
        </Card>
      </div>
    </ScrollablePageContent>
  );
}
