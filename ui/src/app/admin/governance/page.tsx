"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ShieldCheck, ShieldX } from "lucide-react";

import {
  InterceptionsChart,
  PatrolTrendChart,
  RemediationStatusChart,
} from "@/components/admin/governance-overview-charts";
import { AdminStatCard } from "@/components/admin/stat-card";
import { AdminTimeRangePicker } from "@/components/admin/time-range-picker";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { type AdminTimeRange, getAdminDays } from "@/lib/admin-utils";
import { complianceApi, type GovernanceOverview } from "@/lib/api/compliance";
import { IconActivity, IconAudit, IconSecurity } from "@/lib/icons";

export default function AdminGovernancePage() {
  const t = useTranslations("governanceOverview");
  const [range, setRange] = useState<AdminTimeRange>("30d");
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<GovernanceOverview | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const data = await complianceApi.getGovernanceOverview({ days: getAdminDays(range) });
        if (!cancelled) setOverview(data);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [range]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-9 w-72" />
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28 rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-72 rounded-xl" />
      </div>
    );
  }

  const totalInterceptions =
    overview?.interceptions.reduce((sum, day) => sum + day.approval_decisions + day.denials, 0) ?? 0;
  const avgDecisionSeconds = overview?.approvals.avg_decision_seconds;
  const chainOk = overview?.chain.ok ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        bordered={false}
        title={t("pageTitle")}
        description={t("pageDescription")}
        actions={<AdminTimeRangePicker value={range} onChange={setRange} />}
      />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <AdminStatCard
          label={t("statPendingApprovals")}
          value={overview?.approvals.pending_count ?? 0}
          hint={t("statOutcomesHint", {
            approved: overview?.approvals.outcomes.approved ?? 0,
            rejected: overview?.approvals.outcomes.rejected ?? 0,
          })}
          icon={IconSecurity}
        />
        <AdminStatCard
          label={t("statAvgDecisionTime")}
          value={avgDecisionSeconds == null ? t("noData") : `${Math.round(avgDecisionSeconds)}s`}
          hint={t("statAvgDecisionTimeHint")}
          icon={IconActivity}
        />
        <AdminStatCard
          label={t("statInterceptions")}
          value={totalInterceptions}
          hint={t("statInterceptionsHint")}
          icon={IconAudit}
        />
        <Card className="gap-0 py-4">
          <CardHeader className="flex flex-row items-start justify-between pb-2">
            <CardTitle className="text-muted-foreground text-sm font-medium">{t("statChainStatus")}</CardTitle>
          </CardHeader>
          <CardContent>
            {chainOk == null ? (
              <div className="text-2xl font-semibold tracking-tight">{t("noData")}</div>
            ) : (
              <StatusBadge variant={chainOk ? "success" : "destructive"} className="gap-1">
                {chainOk ? <ShieldCheck className="size-3.5" /> : <ShieldX className="size-3.5" />}
                {chainOk ? t("chainIntact") : t("chainBroken")}
              </StatusBadge>
            )}
            {overview?.chain.first_broken_seq != null ? (
              <p className="text-muted-foreground mt-1 text-xs">
                {t("chainBrokenAt", { seq: overview.chain.first_broken_seq })}
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <InterceptionsChart data={overview?.interceptions ?? []} />
        <PatrolTrendChart data={overview?.patrol ?? []} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <RemediationStatusChart
          remediation={
            overview?.remediation ?? {
              by_status: {
                proposed: 0,
                executing: 0,
                executed: 0,
                verified: 0,
                failed: 0,
                cancelled: 0,
              },
              success_rate: null,
            }
          }
        />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("approvalOutcomesTitle")}</CardTitle>
            <CardDescription>{t("approvalOutcomesDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <MetricRow label={t("outcomeApproved")} value={overview?.approvals.outcomes.approved ?? 0} />
            <MetricRow label={t("outcomeRejected")} value={overview?.approvals.outcomes.rejected ?? 0} />
            <MetricRow label={t("outcomeExpired")} value={overview?.approvals.outcomes.expired ?? 0} />
            <MetricRow label={t("outcomeConsumed")} value={overview?.approvals.outcomes.consumed ?? 0} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-muted/30 rounded-lg border px-3 py-3">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}
