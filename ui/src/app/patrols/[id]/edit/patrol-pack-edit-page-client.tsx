"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { AsyncBoundary } from "@/components/async-boundary";
import { PageHeader } from "@/components/page-header";
import { PackWizard } from "@/components/patrol/pack-wizard";
import { ScrollablePageContent } from "@/components/scrollable-page-content";

import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolPack } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";
import { useReportPageTitle } from "@/providers/page-title-provider";

export function PatrolPackEditPageClient({ id }: { id: string }) {
  const t = useTranslations("patrol");
  const { user, loading: authLoading } = useAuth();
  const [pack, setPack] = useState<PatrolPack | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 重试计数：错误态点"重试"时递增以重跑加载 effect。
  const [reloadKey, setReloadKey] = useState(0);
  useReportPageTitle(pack?.name);
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const item = await patrolsApi.getPack(id);
        if (cancelled) return;
        setPack(item);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : t("errors.load"));
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [id, t, reloadKey]);
  if (authLoading) return null;
  if (user?.global_role === "auditor")
    return (
      <ScrollablePageContent>
        <p>{t("new.readOnly")}</p>
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <div className="grid gap-5">
        <PageHeader title={t("edit.title")} description={t("edit.description")} />
        <AsyncBoundary
          loading={!pack && !error}
          error={error}
          onRetry={() => {
            setError(null);
            setReloadKey((key) => key + 1);
          }}
        >
          {pack ? <PackWizard pack={pack} /> : null}
        </AsyncBoundary>
      </div>
    </ScrollablePageContent>
  );
}
