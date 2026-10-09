"use client";
import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import type { EvaluationAccess } from "@/components/evaluation/evaluation-boundary";

import { useCapabilities } from "@/hooks/use-capabilities";
import { hasExecutionGrant } from "@/lib/api/capabilities";
import { teamApi } from "@/lib/api/team";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";
export type AnalysisAccess = EvaluationAccess & {
  ownerKey: string;
  callerId?: string;
  canWritePreferences?: boolean;
};
export function AnalysisBoundary({
  children,
}: {
  children: (access: AnalysisAccess) => React.ReactNode;
}) {
  const t = useTranslations("analysis");
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const { snapshot, loading: checking } = useCapabilities();
  const key = JSON.stringify([user?.id, scope, scopeRevision]);
  const [denied, setDenied] = useState<string | null>(null);
  const deny = useCallback(() => setDenied(key), [key]);
  const [preferenceRole, setPreferenceRole] = useState<{ key: string; allowed: boolean } | null>(
    null,
  );
  const userId = user?.id;
  const globalRole = user?.global_role;
  const workspaceId = scope?.workspaceId;
  useEffect(() => {
    if (!workspaceId || !userId || globalRole === "auditor") return;
    const abort = new AbortController();
    void teamApi
      .members(workspaceId, { workspaceId, signal: abort.signal })
      .then(({ members }) => {
        if (!abort.signal.aborted)
          setPreferenceRole({
            key,
            allowed: members.some(
              (m) => m.user_id === userId && ["owner", "admin"].includes(m.role),
            ),
          });
      })
      .catch(() => {
        if (!abort.signal.aborted) setPreferenceRole({ key, allowed: false });
      });
    return () => abort.abort();
  }, [key, workspaceId, userId, globalRole]);

  if (loading || (checking && !snapshot)) return <p role="status">{t("loading")}</p>;
  if (
    !user ||
    !scope ||
    scope.userId !== user.id ||
    denied === key ||
    !hasExecutionGrant(snapshot ?? undefined, "execution.read")
  )
    return <p role="alert">{t("denied")}</p>;
  return (
    <div key={key} className="h-full min-w-0 overflow-y-auto">
      <main className="mx-auto max-w-7xl space-y-6 p-3 pb-24 sm:p-6">
        {children({
          ownerKey: key,
          callerId: user.id,
          workspaceId: scope.workspaceId,
          canManage: user.global_role !== "auditor",
          canWritePreferences:
            user.global_role !== "auditor" &&
            (!scope.workspaceId || (preferenceRole?.key === key && preferenceRole.allowed)),
          canRun: false,
          canRegister: false,
          deny,
        })}
      </main>
    </div>
  );
}
