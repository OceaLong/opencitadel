"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertTriangle, CheckCircle2, Clock3, Pause, Play, Stethoscope } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { usePatrolLabels } from "@/hooks/use-patrol-labels";
import type { PatrolPack, PatrolRun } from "@/lib/api/types";

const variant = (status: string) =>
  status === "active" || status === "completed"
    ? "success"
    : status === "invalid" || status === "failed"
      ? "destructive"
      : status.includes("finding") || status === "paused"
        ? "warning"
        : "secondary";

export function PatrolPackList({
  packs,
  latestRuns,
  loading,
  onTrigger,
  onToggle,
  triggeringId,
  actionId,
  readOnly,
}: {
  packs: PatrolPack[];
  latestRuns: Record<string, PatrolRun>;
  loading: boolean;
  onTrigger: (pack: PatrolPack) => void;
  onToggle: (pack: PatrolPack) => void;
  triggeringId: string | null;
  actionId: string | null;
  readOnly: boolean;
}) {
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  if (loading) {
    return (
      <div className="grid gap-3">
        {[0, 1, 2].map((item) => (
          <Skeleton key={item} className="h-32 w-full rounded-xl" />
        ))}
      </div>
    );
  }
  if (!packs.length) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex min-h-64 flex-col items-center justify-center gap-4 text-center">
          <Stethoscope className="text-muted-foreground size-10" />
          <div>
            <h2 className="font-semibold">{t("empty.title")}</h2>
            <p className="text-muted-foreground mt-1 text-sm">{t("empty.description")}</p>
          </div>
          {!readOnly && (
            <Button asChild>
              <Link href="/patrols/new">{t("actions.create")}</Link>
            </Button>
          )}
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="grid gap-3">
      {packs.map((pack) => {
        const run = latestRuns[pack.id];
        return (
          <Card key={pack.id}>
            <CardContent className="grid gap-4 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={`/patrols/${pack.id}`}
                    className="truncate font-semibold hover:underline"
                  >
                    {pack.name}
                  </Link>
                  <StatusBadge variant={variant(pack.status)}>
                    {labels.status[pack.status] ?? pack.status}
                  </StatusBadge>
                  <span className="text-muted-foreground text-xs">
                    v{pack.version} · {pack.config.scope.environment}
                  </span>
                </div>
                <div className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-1 text-xs">
                  <span className="inline-flex items-center gap-1">
                    <Clock3 className="size-3.5" />
                    {pack.next_run_at
                      ? t("nextRun", { value: new Date(pack.next_run_at).toLocaleString() })
                      : t("scheduleOff")}{" "}
                    · {pack.config.timezone}
                  </span>
                  <span>
                    {pack.config.scope.cluster} / {pack.config.scope.namespaces.join(", ")}
                  </span>
                </div>
                {run ? (
                  <div className="flex flex-wrap items-center gap-3 text-xs">
                    <StatusBadge variant={variant(run.status)}>
                      {labels.status[run.status] ?? run.status}
                    </StatusBadge>
                    <span className="inline-flex items-center gap-1">
                      <CheckCircle2 className="size-3.5" />
                      PASS {run.counts.pass}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <AlertTriangle className="size-3.5" />
                      WARN {run.counts.warn} · FAIL {run.counts.fail} · ERROR {run.counts.error}
                    </span>
                    <span>
                      {t("evidencePercent", {
                        value: Math.round((run.evidence_completeness ?? 0) * 100),
                      })}
                    </span>
                  </div>
                ) : (
                  <p className="text-muted-foreground text-xs">{t("empty.noRuns")}</p>
                )}
              </div>
              <div className="flex gap-2">
                <Button variant="outline" asChild>
                  <Link href={`/patrols/${pack.id}`}>{t("actions.view")}</Link>
                </Button>
                {!readOnly && (
                  <>
                    <Button
                      variant="outline"
                      disabled={
                        actionId === pack.id ||
                        (pack.status !== "active" && pack.last_validated_version !== pack.version)
                      }
                      onClick={() => onToggle(pack)}
                    >
                      {pack.status === "active" ? (
                        <Pause className="size-4" />
                      ) : (
                        <Play className="size-4" />
                      )}
                      {pack.status === "active" ? t("actions.pause") : t("actions.activate")}
                    </Button>
                    <Button
                      disabled={pack.status !== "active" || triggeringId === pack.id}
                      onClick={() => onTrigger(pack)}
                    >
                      <Play className="size-4" />
                      {t("actions.runNow")}
                    </Button>
                  </>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
