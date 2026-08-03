"use client";

import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/page-header";
import { PackWizard } from "@/components/patrol/pack-wizard";
import { ScrollablePageContent } from "@/components/scrollable-page-content";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
import { useAuth } from "@/providers/auth-provider";

export default function NewPatrolPage() {
  const t = useTranslations("patrol");
  const { opsPatrolEnabled, loading } = useFeatureFlags();
  const { user, loading: authLoading } = useAuth();
  if (loading || authLoading) return null;
  if (!opsPatrolEnabled)
    return (
      <ScrollablePageContent>
        <p className="p-6">{t("disabled.title")}</p>
      </ScrollablePageContent>
    );
  if (user?.global_role === "auditor")
    return (
      <ScrollablePageContent>
        <p className="p-6">{t("new.readOnly")}</p>
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <div className="grid gap-5 p-4 sm:p-6">
        <PageHeader bordered={false} title={t("new.title")} description={t("new.description")} />
        <PackWizard />
      </div>
    </ScrollablePageContent>
  );
}
