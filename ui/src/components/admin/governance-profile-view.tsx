"use client";

import { useTranslations } from "next-intl";
import { ShieldCheck, ShieldX } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { formatDateTime } from "@/lib/admin-utils";
import type { GovernanceProfile } from "@/lib/api/compliance";

type GovernanceProfileViewProps = {
  profile: GovernanceProfile;
};

// Session terminal states: completed -> green, failed -> red, everything
// else (cancelled, or any non-terminal status the profile still shows) ->
// neutral gray, matching StatusBadge's "secondary" default look.
function terminalStatusVariant(status: string): "success" | "destructive" | "secondary" {
  if (status === "completed") return "success";
  if (status === "failed") return "destructive";
  return "secondary";
}

export function GovernanceProfileView({ profile }: GovernanceProfileViewProps) {
  const t = useTranslations("governanceProfile");
  const { session, chain, approvals, gate_hits: gateHits, checkpoints, terminal } = profile;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("summary")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-muted-foreground text-xs">{t("status")}</p>
            <StatusBadge
              data-testid="terminal-status-badge"
              variant={terminalStatusVariant(terminal.status)}
            >
              {terminal.status}
            </StatusBadge>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("gateProfile")}</p>
            {session.gate_profile ? (
              <Badge variant="secondary">{session.gate_profile}</Badge>
            ) : (
              <span className="text-muted-foreground text-sm">—</span>
            )}
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("operatorScope")}</p>
            {session.operator_scope ? (
              <Badge variant="outline">{session.operator_scope}</Badge>
            ) : (
              <span className="text-muted-foreground text-sm">—</span>
            )}
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("chainVerification")}</p>
            <StatusBadge variant={chain.verified ? "success" : "destructive"} className="gap-1">
              {chain.verified ? <ShieldCheck className="size-3.5" /> : <ShieldX className="size-3.5" />}
              {chain.verified ? t("chainVerified") : t("chainBroken")}
            </StatusBadge>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("terminalState")}</p>
            <p className="text-sm">
              {terminal.status} · {formatDateTime(terminal.reached_at)}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("approvals")}</CardTitle>
        </CardHeader>
        <CardContent>
          {approvals.length === 0 ? (
            <EmptyState title={t("noData")} className="py-8" />
          ) : (
            <ol className="space-y-3">
              {approvals.map((approval, index) => (
                <li
                  key={`${approval.action}-${approval.created_at}-${index}`}
                  className="border-border/50 rounded-lg border p-3 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-muted-foreground text-xs">
                      {formatDateTime(approval.created_at)}
                    </span>
                    <Badge variant="outline">{approval.action}</Badge>
                    {approval.decision ? (
                      <StatusBadge
                        variant={approval.decision === "reject" ? "destructive" : "success"}
                      >
                        {approval.decision}
                      </StatusBadge>
                    ) : null}
                  </div>
                  <div className="text-muted-foreground mt-2 grid gap-1 text-xs sm:grid-cols-2">
                    <span>
                      {t("tool")}: {approval.tool ?? "—"}
                    </span>
                    <span>
                      {t("actor")}: {approval.actor_user_id ?? "—"}
                    </span>
                    {approval.feedback ? (
                      <span className="sm:col-span-2">
                        {t("feedback")}: {approval.feedback}
                      </span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("gateHits")}</CardTitle>
        </CardHeader>
        <CardContent>
          {gateHits.length === 0 ? (
            <EmptyState title={t("noData")} className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("tool")}</th>
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("gateProfile")}</th>
                    <th className="text-muted-foreground pb-2 font-medium">{t("time")}</th>
                  </tr>
                </thead>
                <tbody>
                  {gateHits.map((hit, index) => (
                    <tr
                      key={`${hit.tool}-${hit.created_at}-${index}`}
                      className="border-border/50 border-b"
                    >
                      <td className="py-2 pr-4">{hit.tool ?? "—"}</td>
                      <td className="py-2 pr-4">
                        {hit.gate_profile ? (
                          <Badge variant="secondary">{hit.gate_profile}</Badge>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="py-2 text-muted-foreground">{formatDateTime(hit.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("checkpoints")}</CardTitle>
        </CardHeader>
        <CardContent>
          {checkpoints.length === 0 ? (
            <EmptyState title={t("noData")} className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("label")}</th>
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("anchorType")}</th>
                    <th className="text-muted-foreground pb-2 font-medium">{t("time")}</th>
                  </tr>
                </thead>
                <tbody>
                  {checkpoints.map((checkpoint) => (
                    <tr key={checkpoint.id} className="border-border/50 border-b">
                      <td className="py-2 pr-4">{checkpoint.label ?? "—"}</td>
                      <td className="py-2 pr-4">
                        <Badge variant="outline">{checkpoint.anchor_type}</Badge>
                      </td>
                      <td className="py-2 text-muted-foreground">
                        {formatDateTime(checkpoint.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
