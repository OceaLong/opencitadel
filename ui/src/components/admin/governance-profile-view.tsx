"use client";

import { useTranslations } from "next-intl";
import { ShieldCheck, ShieldX } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { formatDateTime } from "@/lib/admin-utils";
import type { GovernanceProfile } from "@/lib/api/compliance";

type Props = { profile: GovernanceProfile };

function statusVariant(status: string): "success" | "destructive" | "secondary" {
  if (status === "completed" || status === "succeeded" || status === "approved") {
    return "success";
  }
  if (status === "failed" || status === "unknown" || status === "rejected") {
    return "destructive";
  }
  return "secondary";
}

export function GovernanceProfileView({ profile }: Props) {
  const t = useTranslations("governanceProfile");
  const { session, chain, runs, approvals, activities } = profile;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("summary")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <div>
            <p className="text-muted-foreground text-xs">{t("status")}</p>
            <StatusBadge data-testid="session-status-badge" variant={statusVariant(session.status)}>
              {session.status}
            </StatusBadge>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("ownerUser")}</p>
            <p className="font-mono text-xs">{session.owner_user_id ?? "—"}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("team")}</p>
            <p className="font-mono text-xs">{session.team_id ?? "—"}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("operatorScope")}</p>
            {session.operator_scope ? (
              <Badge variant="outline">{session.operator_scope}</Badge>
            ) : (
              "—"
            )}
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("operatorDomains")}</p>
            <p className="text-sm">{session.operator_domains.join(", ") || "—"}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("chainVerification")}</p>
            <StatusBadge variant={chain.verified ? "success" : "destructive"} className="gap-1">
              {chain.verified ? (
                <ShieldCheck className="size-3.5" />
              ) : (
                <ShieldX className="size-3.5" />
              )}
              {chain.verified ? t("chainVerified") : t("chainBroken")}
            </StatusBadge>
            <p className="text-muted-foreground mt-1 text-xs">
              {t("chainCounts", { runs: chain.checked_runs, events: chain.checked_entries })}
            </p>
          </div>
        </CardContent>
      </Card>

      <TimelineCard title={t("runs")} empty={runs.length === 0}>
        {runs.map((run) => (
          <li key={run.run_id} className="border-border/50 rounded-lg border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant={statusVariant(run.status)}>{run.status}</StatusBadge>
              <Badge variant="outline">{run.family}</Badge>
              <span className="font-mono text-xs">{run.run_id}</span>
            </div>
            <p className="text-muted-foreground mt-2 text-xs">{formatDateTime(run.created_at)}</p>
          </li>
        ))}
      </TimelineCard>

      <TimelineCard title={t("approvals")} empty={approvals.length === 0}>
        {approvals.map((approval) => (
          <li key={approval.approval_id} className="border-border/50 rounded-lg border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant={statusVariant(approval.status)}>{approval.status}</StatusBadge>
              <Badge variant="outline">{approval.approval_kind}</Badge>
              <span>{approval.subject_label}</span>
            </div>
            <div className="text-muted-foreground mt-2 grid gap-1 text-xs sm:grid-cols-2">
              <span>
                {t("actor")}: {approval.decided_by_user_id ?? "—"}
              </span>
              <span>
                {t("time")}: {formatDateTime(approval.requested_at)}
              </span>
              <span className="sm:col-span-2">
                {t("risk")}: {approval.risk_summary}
              </span>
              {approval.feedback ? (
                <span className="sm:col-span-2">
                  {t("feedback")}: {approval.feedback}
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </TimelineCard>

      <TimelineCard title={t("activities")} empty={activities.length === 0}>
        {activities.map((activity) => (
          <li key={activity.activity_id} className="border-border/50 rounded-lg border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant={statusVariant(activity.status)}>{activity.status}</StatusBadge>
              <Badge variant="outline">{activity.activity_type}</Badge>
              <span className="font-mono text-xs">{activity.activity_id}</span>
            </div>
            <div className="text-muted-foreground mt-2 flex flex-wrap gap-4 text-xs">
              <span>
                {t("attempt")}: {activity.attempt}
              </span>
              <span>
                {t("time")}: {formatDateTime(activity.created_at)}
              </span>
              {activity.failure_code ? (
                <span>
                  {t("failureCode")}: {activity.failure_code}
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </TimelineCard>
    </div>
  );
}

function TimelineCard({
  title,
  empty,
  children,
}: {
  title: string;
  empty: boolean;
  children: React.ReactNode;
}) {
  const t = useTranslations("governanceProfile");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {empty ? (
          <EmptyState title={t("noData")} className="py-8" />
        ) : (
          <ol className="space-y-3">{children}</ol>
        )}
      </CardContent>
    </Card>
  );
}
