"use client";

import { useTranslations } from "next-intl";

import { DefinitionManagement } from "@/components/admin/definition-management";

import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

export default function DefinitionsPage() {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const t = useTranslations("adminNav");
  if (loading || !scope || !user || scope.userId !== user.id) return null;
  if (user.global_role !== "admin") return <p role="alert">{t("forbidden")}</p>;
  return (
    <DefinitionManagement
      key={`${user.id}:${scope.workspaceId}:${scopeRevision}`}
      workspaceId={scope.workspaceId}
    />
  );
}
