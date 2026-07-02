"use client";

import { useEffect, useState } from "react";
import { Cpu, MailPlus, PhoneCall, Users } from "lucide-react";

import { AdminStatCard } from "@/components/admin/stat-card";
import { AdminTimeRangePicker } from "@/components/admin/time-range-picker";
import {
  AuditActivityChart,
  UsageBreakdownChart,
  UsageCallsChart,
  UsageTimeseriesChart,
} from "@/components/admin/usage-charts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { type AdminTimeRange, formatDateTime,getAdminDateRange } from "@/lib/admin-utils";
import {
  adminApi,
  type AdminOverview,
  type AuditLog,
  type PlatformInvitation,
  type UsageSummary,
  type UsageTimeseriesPoint,
} from "@/lib/api/admin";

export default function AdminOverviewPage() {
  const [range, setRange] = useState<AdminTimeRange>("30d");
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [timeseries, setTimeseries] = useState<UsageTimeseriesPoint[]>([]);
  const [modelBreakdown, setModelBreakdown] = useState<Array<{ key: string; total_tokens: number; call_count: number }>>([]);
  const [userBreakdown, setUserBreakdown] = useState<Array<{ key: string; total_tokens: number; call_count: number }>>([]);
  const [recentAudit, setRecentAudit] = useState<AuditLog[]>([]);
  const [auditByDay, setAuditByDay] = useState<Array<{ date: string; count: number }>>([]);
  const [recentInvitations, setRecentInvitations] = useState<PlatformInvitation[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      const dateParams = getAdminDateRange(range);
      try {
        const [
          overviewData,
          usageData,
          timeseriesData,
          modelData,
          userData,
          auditData,
          auditSummary,
          invitationData,
        ] = await Promise.all([
          adminApi.overview(),
          adminApi.usageSummary(dateParams),
          adminApi.usageTimeseries(dateParams),
          adminApi.usageBreakdown("model", { ...dateParams, limit: 8 }),
          adminApi.usageBreakdown("user", { ...dateParams, limit: 8 }),
          adminApi.audit({ limit: 8 }),
          adminApi.auditSummary(dateParams),
          adminApi.invitations({ limit: 6 }),
        ]);
        if (cancelled) return;
        setOverview(overviewData);
        setUsage(usageData);
        setTimeseries(timeseriesData.points);
        setModelBreakdown(modelData.items);
        setUserBreakdown(userData.items);
        setRecentAudit(auditData.logs);
        setAuditByDay(auditSummary.by_day);
        setRecentInvitations(invitationData.invitations);
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">概览</h2>
          <p className="text-muted-foreground mt-1 text-sm">平台用户、用量与运营状态总览</p>
        </div>
        <AdminTimeRangePicker value={range} onChange={setRange} />
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <AdminStatCard label="总用户" value={overview?.total_users ?? 0} hint={`活跃 ${overview?.active_users ?? 0}`} icon={Users} />
        <AdminStatCard label="Total Tokens" value={usage?.total_tokens ?? 0} hint={`Prompt ${usage?.prompt_tokens ?? 0}`} icon={Cpu} />
        <AdminStatCard label="LLM 调用" value={usage?.call_count ?? 0} hint={`缓存 ${usage?.cached_tokens ?? 0}`} icon={PhoneCall} />
        <AdminStatCard label="待处理邀请" value={overview?.pending_invitations ?? 0} hint={`已接受 ${overview?.accepted_invitations ?? 0}`} icon={MailPlus} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <UsageTimeseriesChart points={timeseries} />
        <UsageCallsChart points={timeseries} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <UsageBreakdownChart title="模型用量分布" description="按 model 聚合 Top 8" items={modelBreakdown} />
        <UsageBreakdownChart title="用户用量分布" description="按 user 聚合 Top 8" items={userBreakdown} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <AuditActivityChart byDay={auditByDay} />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">平台概况</CardTitle>
            <CardDescription>用户与邀请状态</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <MetricRow label="管理员" value={overview?.admin_users ?? 0} />
            <MetricRow label="禁用用户" value={overview?.disabled_users ?? 0} />
            <MetricRow label="已接受邀请" value={overview?.accepted_invitations ?? 0} />
            <MetricRow label="过期邀请" value={overview?.expired_invitations ?? 0} />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">最近审计</CardTitle>
            <CardDescription>最近 8 条管理操作</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {recentAudit.length === 0 ? (
              <EmptyHint text="暂无审计记录" />
            ) : (
              recentAudit.map((item) => (
                <div key={item.id} className="rounded-lg border px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{item.action}</span>
                    <span className="text-muted-foreground text-xs">{formatDateTime(item.created_at)}</span>
                  </div>
                  <div className="text-muted-foreground mt-1 text-xs">
                    {item.resource_type}:{item.resource_id}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">最近邀请</CardTitle>
            <CardDescription>最近 6 条平台邀请</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {recentInvitations.length === 0 ? (
              <EmptyHint text="暂无邀请记录" />
            ) : (
              recentInvitations.map((item) => (
                <div key={item.id} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{item.email || "未指定邮箱"}</div>
                    <div className="text-muted-foreground text-xs">{formatDateTime(item.created_at)}</div>
                  </div>
                  <InvitationStatusBadge status={item.status} />
                </div>
              ))
            )}
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

function EmptyHint({ text }: { text: string }) {
  return <div className="text-muted-foreground py-8 text-center text-sm">{text}</div>;
}

function InvitationStatusBadge({ status }: { status: PlatformInvitation["status"] }) {
  const variant = status === "accepted" ? "secondary" : status === "pending" ? "outline" : "destructive";
  const label = status === "accepted" ? "已接受" : status === "pending" ? "待注册" : "已过期";
  return <Badge variant={variant}>{label}</Badge>;
}
