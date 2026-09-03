"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

import { usePatrolLabels } from "@/hooks/use-patrol-labels";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolRemediation, PatrolRemediationStatus } from "@/lib/api/types";

// Six-state coverage is load-bearing: every PatrolRemediationStatus value
// (api/app/domain/models/patrol.py::PatrolRemediationStatus) must map to a
// badge here so a new/unmapped status can never render blank. Colors mirror
// the phase-3 brief: PROPOSED/CANCELLED gray, EXECUTING/EXECUTED blue,
// VERIFIED green, FAILED red.
const IN_PROGRESS_BADGE_CLASSNAME = "border-transparent bg-info-subtle text-info";

const REMEDIATION_BADGE: Record<
  PatrolRemediationStatus,
  { variant: "secondary" | "outline" | "success" | "destructive"; className?: string }
> = {
  proposed: { variant: "secondary" },
  executing: { variant: "outline", className: IN_PROGRESS_BADGE_CLASSNAME },
  executed: { variant: "outline", className: IN_PROGRESS_BADGE_CLASSNAME },
  verified: { variant: "success" },
  failed: { variant: "destructive" },
  cancelled: { variant: "secondary" },
};

export function RemediationStatusList({ remediations }: { remediations: PatrolRemediation[] }) {
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  const [detail, setDetail] = useState<PatrolRemediation | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const openDetail = async (id: string) => {
    setDetailLoadingId(id);
    try {
      setDetail(await patrolsApi.getRemediation(id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("remediation.errors.detailLoad"));
    } finally {
      setDetailLoadingId(null);
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("remediation.title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {remediations.length === 0 ? (
          <EmptyState title={t("remediation.empty")} />
        ) : (
          remediations.map((remediation) => {
            const badge = REMEDIATION_BADGE[remediation.status];
            return (
              <div
                key={remediation.id}
                className="grid gap-2 rounded-lg border p-4 sm:grid-cols-[1fr_auto]"
              >
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge variant={badge.variant} className={badge.className}>
                      {labels.remediationStatus[remediation.status] ?? remediation.status}
                    </StatusBadge>
                    <span className="text-sm font-medium">
                      {labels.remediationAction[remediation.action] ?? remediation.action}
                    </span>
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {remediation.target_kind}/{remediation.target_workload || "—"} ·{" "}
                    {remediation.target_namespace}
                  </p>
                  {remediation.impact_summary && (
                    <p className="mt-1 text-xs">
                      <span className="text-muted-foreground">
                        {t("remediation.impactSummaryLabel")}:{" "}
                      </span>
                      {remediation.impact_summary}
                    </p>
                  )}
                  {remediation.rollback_hint && (
                    <p className="mt-1 text-xs">
                      <span className="text-muted-foreground">
                        {t("remediation.rollbackHintLabel")}:{" "}
                      </span>
                      {remediation.rollback_hint}
                    </p>
                  )}
                  {remediation.error_message && (
                    <p className="text-destructive mt-1 text-xs">{remediation.error_message}</p>
                  )}
                </div>
                <div className="flex flex-col items-start gap-1 text-xs sm:items-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-auto p-0 text-xs underline"
                    disabled={detailLoadingId === remediation.id}
                    onClick={() => void openDetail(remediation.id)}
                  >
                    {detailLoadingId === remediation.id ? (
                      <LoadingSpinner />
                    ) : (
                      t("remediation.viewDetail")
                    )}
                  </Button>
                  {remediation.session_id && (
                    <Link
                      className="text-primary underline"
                      href={`/sessions/${remediation.session_id}`}
                    >
                      {t("remediation.viewSession")}
                    </Link>
                  )}
                  {remediation.recheck_run_id && (
                    <Link
                      className="text-primary underline"
                      href={`/patrol-runs/${remediation.recheck_run_id}`}
                    >
                      {t("remediation.viewRecheckRun")}
                    </Link>
                  )}
                </div>
              </div>
            );
          })
        )}
      </CardContent>
      <Dialog open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("remediation.detail.title")}</DialogTitle>
          </DialogHeader>
          {detail && (
            <div className="grid gap-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  variant={REMEDIATION_BADGE[detail.status].variant}
                  className={REMEDIATION_BADGE[detail.status].className}
                >
                  {labels.remediationStatus[detail.status] ?? detail.status}
                </StatusBadge>
                <span className="font-medium">
                  {labels.remediationAction[detail.action] ?? detail.action}
                </span>
              </div>
              <p className="text-muted-foreground text-xs" translate="no">
                {detail.target_kind}/{detail.target_workload || "—"} · {detail.target_namespace}
              </p>
              {detail.error_message && (
                <p className="text-destructive text-xs">{detail.error_message}</p>
              )}
              <div>
                <p className="font-medium">{t("remediation.detail.paramsLabel")}</p>
                <pre className="bg-muted mt-1 max-h-48 overflow-auto rounded p-3 text-xs">
                  {JSON.stringify(detail.params, null, 2)}
                </pre>
              </div>
              <div>
                <p className="font-medium">{t("remediation.detail.beforeLabel")}</p>
                <pre className="bg-muted mt-1 max-h-48 overflow-auto rounded p-3 text-xs">
                  {JSON.stringify(detail.before_observation ?? null, null, 2)}
                </pre>
              </div>
              <div>
                <p className="font-medium">{t("remediation.detail.afterLabel")}</p>
                <pre className="bg-muted mt-1 max-h-48 overflow-auto rounded p-3 text-xs">
                  {JSON.stringify(detail.after_observation ?? null, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
