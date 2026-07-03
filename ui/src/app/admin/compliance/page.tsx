"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, Loader2, ShieldCheck, ShieldX } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { complianceApi, type EvidenceSessionItem } from "@/lib/api/compliance";

export default function AdminCompliancePage() {
  const t = useTranslations("compliance");
  const tCommon = useTranslations("common");
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
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">{t("evidenceCenterTitle")}</h2>
            <p className="text-muted-foreground mt-1 text-sm">{t("evidenceCenterDesc")}</p>
          </div>
          <Badge variant={chainOk ? "default" : "destructive"} className="gap-1">
            {chainOk ? <ShieldCheck className="size-3.5" /> : <ShieldX className="size-3.5" />}
            {chainOk ? t("chainIntact") : t("chainBroken")}
          </Badge>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("evidenceSessionsTitle")}</CardTitle>
            <CardDescription>{t("evidenceSessionsDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 className="size-4 animate-spin" />
                {tCommon("loading")}
              </div>
            ) : sessions.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("noEvidenceSessions")}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left">
                      <th className="pb-2 pr-4 font-medium">{t("colTitle")}</th>
                      <th className="pb-2 pr-4 font-medium">{t("colScope")}</th>
                      <th className="pb-2 pr-4 font-medium">{t("colGate")}</th>
                      <th className="pb-2 pr-4 font-medium">{t("colChain")}</th>
                      <th className="pb-2 font-medium">{t("colActions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => (
                      <tr key={s.session_id} className="border-b border-border/50">
                        <td className="py-3 pr-4">
                          <Link
                            href={`/sessions/${s.session_id}`}
                            className="text-primary hover:underline"
                          >
                            {s.title || s.session_id}
                          </Link>
                        </td>
                        <td className="py-3 pr-4">{s.operator_scope ?? "—"}</td>
                        <td className="py-3 pr-4">{s.gate_profile ?? "—"}</td>
                        <td className="py-3 pr-4">
                          <Badge variant={s.chain_ok ? "secondary" : "destructive"}>
                            {s.chain_ok ? t("chainOk") : t("chainFail")}
                          </Badge>
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
