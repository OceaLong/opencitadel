"use client";

import { useTranslations } from "next-intl";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { formatShortDate } from "@/lib/admin-utils";
import type {
  GovernanceDailyCount,
  GovernanceDailyPatrolStat,
  RemediationStats,
} from "@/lib/api/compliance";

const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; color?: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-popover text-popover-foreground rounded-lg border px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: entry.color }} />
          <span>{entry.name}</span>
          <span className="font-medium">{entry.value ?? 0}</span>
        </div>
      ))}
    </div>
  );
}

export function InterceptionsChart({ data }: { data: GovernanceDailyCount[] }) {
  const t = useTranslations("governanceOverview");
  const points = data.map((point) => ({ ...point, label: formatShortDate(point.date) }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("interceptionsTitle")}</CardTitle>
        <CardDescription>{t("interceptionsDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {points.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t("noData")}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={points}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
              <XAxis dataKey="label" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} width={40} />
              <Tooltip content={<ChartTooltip />} />
              <Legend />
              <Bar dataKey="approval_decisions" name={t("approvalDecisions")} fill={CHART_COLORS[0]} radius={[6, 6, 0, 0]} />
              <Bar dataKey="denials" name={t("denials")} fill={CHART_COLORS[3]} radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export function PatrolTrendChart({ data }: { data: GovernanceDailyPatrolStat[] }) {
  const t = useTranslations("governanceOverview");
  const points = data.map((point) => ({ ...point, label: formatShortDate(point.date) }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("patrolTrendTitle")}</CardTitle>
        <CardDescription>{t("patrolTrendDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {points.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t("noData")}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={points}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
              <XAxis dataKey="label" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} width={40} />
              <Tooltip content={<ChartTooltip />} />
              <Legend />
              <Bar dataKey="runs" name={t("patrolRuns")} fill={CHART_COLORS[1]} radius={[6, 6, 0, 0]} />
              <Bar dataKey="findings" name={t("patrolFindings")} fill={CHART_COLORS[2]} radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export function RemediationStatusChart({ remediation }: { remediation: RemediationStats }) {
  const t = useTranslations("governanceOverview");
  const statusLabels: Record<keyof RemediationStats["by_status"], string> = {
    proposed: t("remediationProposed"),
    executing: t("remediationExecuting"),
    executed: t("remediationExecuted"),
    verified: t("remediationVerified"),
    failed: t("remediationFailed"),
    cancelled: t("remediationCancelled"),
  };
  const data = (Object.keys(statusLabels) as Array<keyof RemediationStats["by_status"]>)
    .map((status) => ({ name: statusLabels[status], value: remediation.by_status[status] }))
    .filter((item) => item.value > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("remediationTitle")}</CardTitle>
        <CardDescription>
          {remediation.success_rate == null
            ? t("remediationDescNoRate")
            : t("remediationDescWithRate", { rate: Math.round(remediation.success_rate * 100) })}
        </CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {data.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t("noData")}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" innerRadius={56} outerRadius={92} paddingAngle={2}>
                {data.map((entry, index) => (
                  <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip content={<ChartTooltip />} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
