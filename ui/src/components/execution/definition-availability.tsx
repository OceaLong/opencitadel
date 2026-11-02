"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { DefinitionAvailability } from "@/lib/api/types/execution-view";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

/** Availability is a live routing observation, never part of an as-of Run view. */
export function ExecutionDefinitionAvailability({
  runId,
  historical = false,
}: {
  runId: string;
  historical?: boolean;
}) {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  if (loading || !user || !scope || scope.userId !== user.id || !runId || historical) return null;
  return (
    <DefinitionAvailabilityNotice
      key={`${user.id}:${scope.workspaceId}:${scopeRevision}:${runId}`}
      runId={runId}
      workspaceId={scope.workspaceId}
    />
  );
}

export function DefinitionAvailabilityNotice({
  runId,
  workspaceId,
}: {
  runId: string;
  workspaceId: string;
}) {
  const t = useTranslations("executionContext");
  const [observation, setObservation] = useState<{
    runId: string;
    workspaceId: string;
    value: DefinitionAvailability | null;
    failed: boolean;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending = false;
    let stopped = false;
    const refresh = async () => {
      if (pending || stopped || controller.signal.aborted) return;
      pending = true;
      clearTimeout(timer);
      try {
        const value = await executionViewApi.getDefinitionAvailability(runId, {
          workspaceId,
          signal: controller.signal,
          skipErrorHandler: true,
        });
        if (controller.signal.aborted) return;
        // Do not retain a previous Run's diagnostic across navigation or scope changes.
        if (value.run_id !== runId) throw new Error("availability identity mismatch");
        setObservation({ runId, workspaceId, value, failed: false });
        stopped = value.state === "terminal" || value.state === "unversioned";
      } catch (error) {
        if (controller.signal.aborted) return;
        stopped = error instanceof ApiError && [401, 403, 404].includes(error.code);
        setObservation({ runId, workspaceId, value: null, failed: !stopped });
      } finally {
        pending = false;
        // Capability expiry/recovery can occur without any formal Run event.
        if (!stopped && !controller.signal.aborted) timer = setTimeout(() => void refresh(), 10000);
      }
    };
    const focus = () => void refresh();
    void refresh();
    window.addEventListener("focus", focus);
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener("focus", focus);
    };
  }, [runId, workspaceId]);
  if (observation?.runId !== runId || observation.workspaceId !== workspaceId) return null;
  if (observation.failed) return <p role="status">{t("availabilityFailed")}</p>;
  if (observation.value?.state !== "version_unavailable") return null;
  return (
    <div role="status" className="space-y-1 rounded-md border p-3 text-sm">
      <p className="font-medium">{t("versionUnavailable")}</p>
      <p>{t("versionRecovery")}</p>
      {observation.value.definition_version != null && (
        <p>{t("waitingVersion", { version: observation.value.definition_version })}</p>
      )}
    </div>
  );
}
