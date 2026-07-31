"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeVersionsData } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";

const ACTIVE_BUILD_STATES = new Set(["queued", "running"]);
const BUILD_POLL_INTERVAL_MS = 5000;

type KnowledgeVersionStatusProps = {
  knowledgeBaseId: string;
  onBuildChanged?: () => void;
};

export function KnowledgeVersionStatus({
  knowledgeBaseId,
  onBuildChanged,
}: KnowledgeVersionStatusProps) {
  const t = useTranslations("knowledge");
  const [history, setHistory] = useState<KnowledgeVersionsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [viewingVersionId, setViewingVersionId] = useState<string | null>(null);
  const loadErrorMessage = t("versionStatusLoadError");
  const actionErrorMessage = t("versionActionError");

  const load = useCallback(
    async (background = false) => {
      if (!background) setLoading(true);
      try {
        setHistory(await knowledgeApi.listVersions(knowledgeBaseId));
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : loadErrorMessage);
      } finally {
        setLoading(false);
      }
    },
    [knowledgeBaseId, loadErrorMessage],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const activeBuild = history?.active_build;
  useEffect(() => {
    if (!activeBuild || !ACTIVE_BUILD_STATES.has(activeBuild.state)) return;
    const timer = window.setInterval(() => {
      void load(true);
    }, BUILD_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeBuild, load]);

  if (loading && !history) {
    return (
      <span role="status" aria-label={t("versionStatusLoading")}>
        <IconLoading className="size-3 animate-spin" />
      </span>
    );
  }

  const active = history?.versions.find((version) => version.is_active);
  const historical =
    history?.versions.filter((version) => version.is_published && !version.is_active) ?? [];
  const viewed =
    history?.versions.find((version) => version.id === viewingVersionId) ?? active ?? null;
  const capabilities = viewed
    ? Object.entries(viewed.capabilities).map(
        ([name, enabled]) =>
          `${name}: ${enabled ? t("capabilityAvailable") : t("capabilityUnavailable")}`,
      )
    : [];
  const actionableBuild =
    history?.active_build ??
    history?.versions.find((version) => version.build?.can_retry)?.build ??
    null;

  const runAction = async (action: "retry" | "cancel") => {
    if (!actionableBuild || acting) return;
    setActing(true);
    try {
      if (action === "retry") {
        await knowledgeApi.retryBuild(knowledgeBaseId, actionableBuild.id);
      } else {
        await knowledgeApi.cancelBuild(knowledgeBaseId, actionableBuild.id);
      }
      await load();
      onBuildChanged?.();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : actionErrorMessage);
    } finally {
      setActing(false);
    }
  };

  const createBuild = async () => {
    if (history?.active_build || acting) return;
    setActing(true);
    try {
      await knowledgeApi.createBuild(knowledgeBaseId);
      await load();
      onBuildChanged?.();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : actionErrorMessage);
    } finally {
      setActing(false);
    }
  };

  return (
    <div className="space-y-1 text-xs">
      <p>{active ? t("activeVersion", { version: active.id }) : t("noActiveVersion")}</p>
      {viewed && (
        <p className="text-muted-foreground">
          {t("versionCapabilities", {
            capabilities: capabilities.length ? capabilities.join(", ") : t("noCapabilities"),
          })}
        </p>
      )}
      {actionableBuild && (
        <div className="text-muted-foreground">
          <p>
            {t("candidateBuild", {
              state: actionableBuild.state,
              phase: actionableBuild.phase ?? t("candidatePhasePending"),
              progress: Math.round(actionableBuild.progress * 100),
            })}
          </p>
          {actionableBuild.error_message && (
            <p role="alert" className="text-destructive">
              {actionableBuild.error_message}
            </p>
          )}
          {!!actionableBuild.degraded_reasons.length && (
            <p className="text-amber-600 dark:text-amber-500">
              {t("candidateDegraded", {
                reasons: actionableBuild.degraded_reasons.join(", "),
              })}
            </p>
          )}
          {actionableBuild.heartbeat_at && (
            <p>
              {t("candidateHeartbeat", {
                heartbeat: actionableBuild.heartbeat_at,
              })}
            </p>
          )}
          <div className="mt-1 flex gap-1">
            {actionableBuild.can_retry && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={acting}
                onClick={() => void runAction("retry")}
              >
                {t("retryBuild")}
              </Button>
            )}
            {actionableBuild.can_cancel && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={acting}
                onClick={() => void runAction("cancel")}
              >
                {t("cancelBuild")}
              </Button>
            )}
          </div>
        </div>
      )}
      {!!historical.length && (
        <div className="space-y-1">
          <p className="text-muted-foreground">{t("previousVersions")}</p>
          <div className="flex flex-wrap gap-1">
            {historical.map((version) => (
              <Button
                key={version.id}
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setViewingVersionId(version.id)}
              >
                {t("viewHistoricalVersion", { version: version.id })}
              </Button>
            ))}
          </div>
          {viewingVersionId && (
            <p>{t("viewingHistoricalVersion", { version: viewingVersionId })}</p>
          )}
        </div>
      )}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={acting || Boolean(history?.active_build)}
        onClick={() => void createBuild()}
      >
        {history?.active_build ? t("viewBuild") : t("reindex")}
      </Button>
    </div>
  );
}
