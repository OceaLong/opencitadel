"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { usePatrolLabels } from "@/hooks/use-patrol-labels";
import type { PatrolRemediation, PatrolRemediationStatus } from "@/lib/api/types";

// Six-state coverage is load-bearing: every PatrolRemediationStatus value
// (api/app/domain/models/patrol.py::PatrolRemediationStatus) must map to a
// badge here so a new/unmapped status can never render blank. Colors mirror
// the phase-3 brief: PROPOSED/CANCELLED gray, EXECUTING/EXECUTED blue,
// VERIFIED green, FAILED red.
const REMEDIATION_BADGE: Record<
  PatrolRemediationStatus,
  { variant: "secondary" | "outline" | "success" | "destructive"; className?: string }
> = {
  proposed: { variant: "secondary" },
  executing: {
    variant: "outline",
    className: "border-transparent bg-blue-500/15 text-blue-600 dark:text-blue-400",
  },
  executed: {
    variant: "outline",
    className: "border-transparent bg-blue-500/15 text-blue-600 dark:text-blue-400",
  },
  verified: { variant: "success" },
  failed: { variant: "destructive" },
  cancelled: { variant: "secondary" },
};

export function RemediationStatusList({ remediations }: { remediations: PatrolRemediation[] }) {
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("remediation.title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {remediations.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("remediation.empty")}</p>
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
    </Card>
  );
}
