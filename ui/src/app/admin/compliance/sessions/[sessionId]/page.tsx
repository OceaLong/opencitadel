"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Download } from "lucide-react";

import { GovernanceProfileView } from "@/components/admin/governance-profile-view";
import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";

import { complianceApi, type GovernanceProfile } from "@/lib/api/compliance";

export default function GovernanceProfilePage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const t = useTranslations("governanceProfile");
  const [profile, setProfile] = useState<GovernanceProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await complianceApi.getGovernanceProfile(sessionId);
      setProfile(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [sessionId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={profile?.session.title || t("title")}
        description={t("pageDescription")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/admin/compliance">
                <ArrowLeft className="mr-1 size-3.5" />
                {t("backToEvidence")}
              </Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href={complianceApi.evidencePackageUrl(sessionId)}>
                <Download className="mr-1 size-3.5" />
                {t("downloadEvidence")}
              </a>
            </Button>
          </div>
        }
      />

      {loading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner />
        </div>
      ) : error || !profile ? (
        <EmptyState title={error ?? t("loadFailed")} className="py-12" />
      ) : (
        <GovernanceProfileView profile={profile} />
      )}
    </div>
  );
}
