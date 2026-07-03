"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, ShieldCheck, ShieldX } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { complianceApi, type EvidenceSessionItem } from "@/lib/api/compliance";

export default function AdminCompliancePage() {
  const t = useTranslations("compliance");
  const [sessions, setSessions] = useState<EvidenceSessionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [chainOk, setChainOk] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, chain] = await Promise.all([
        complianceApi.listEvidenceSessions({ limit: 50 }),
        complianceApi.verifyChain(),
      ]);
      setSessions(list.sessions);
      setChainOk(chain.ok);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <PageHeader
        bordered={false}
        title={t("evidenceCenterTitle")}
        description={t("evidenceCenterDesc")}
        actions={
          chainOk != null ? (
            <StatusBadge variant={chainOk ? "success" : "destructive"} className="gap-1">
              {chainOk ? <ShieldCheck className="size-3.5" /> : <ShieldX className="size-3.5" />}
              {chainOk ? t("chainIntact") : t("chainBroken")}
            </StatusBadge>
          ) : null
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("evidenceSessionsTitle")}</CardTitle>
          <CardDescription>{t("evidenceSessionsDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : sessions.length === 0 ? (
            <EmptyState title={t("noEvidenceSessions")} className="py-10" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("colTitle")}</th>
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("colScope")}</th>
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("colGate")}</th>
                    <th className="text-muted-foreground pb-2 pr-4 font-medium">{t("colChain")}</th>
                    <th className="text-muted-foreground pb-2 font-medium">{t("colActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr
                      key={s.session_id}
                      className="border-border/50 hover:bg-muted/40 border-b transition-colors"
                    >
                      <td className="py-3 pr-4">
                        <Link
                          href={`/sessions/${s.session_id}`}
                          className="text-primary hover:underline"
                        >
                          {s.title || s.session_id}
                        </Link>
                      </td>
                      <td className="py-3 pr-4">
                        {s.operator_scope ? (
                          <Badge variant="outline">{s.operator_scope}</Badge>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="py-3 pr-4">
                        {s.gate_profile ? (
                          <Badge variant="secondary">{s.gate_profile}</Badge>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="py-3 pr-4">
                        <StatusBadge variant={s.chain_ok ? "success" : "destructive"}>
                          {s.chain_ok ? t("chainOk") : t("chainFail")}
                        </StatusBadge>
                      </td>
                      <td className="py-3">
                        <Button variant="outline" size="sm" asChild>
                          <a href={complianceApi.evidencePackageUrl(s.session_id)}>
                            <Download className="mr-1 size-3.5" />
                            {t("downloadPackage")}
                          </a>
                        </Button>
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
