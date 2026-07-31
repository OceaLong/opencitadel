"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

import { codebaseApi } from "@/lib/api/codebase";
import type { CodebaseBuild, CodebaseVersionsData } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";

const ACTIVE_BUILD_STATES = new Set(["queued", "running"]);
const BUILD_POLL_INTERVAL_MS = 5000;

type CodebaseVersionStatusProps = {
  codebaseId: string;
  history?: CodebaseVersionsData | null;
  onBuildChanged?: () => void;
};

function unsupportedViews(version: CodebaseVersionsData["versions"][number] | null): string[] {
  const raw = version?.metrics?.unsupported_views;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>).map(
    ([name, reason]) => `${name}: ${String(reason)}`,
  );
}

function isActiveBuild(build: CodebaseBuild | null | undefined): boolean {
  return Boolean(build && ACTIVE_BUILD_STATES.has(build.state));
}

export function CodebaseVersionStatus({
  codebaseId,
  history: controlledHistory,
  onBuildChanged,
}: CodebaseVersionStatusProps) {
  const t = useTranslations("codebase");
  const [loadedHistory, setLoadedHistory] = useState<CodebaseVersionsData | null>(null);
  const [loading, setLoading] = useState(controlledHistory === undefined);
  const [acting, setActing] = useState(false);
  const [viewingVersionId, setViewingVersionId] = useState<string | null>(null);
  const history = controlledHistory === undefined ? loadedHistory : controlledHistory;
  const loadErrorMessage = t("versionStatusLoadError");
  const actionErrorMessage = t("versionActionError");

  const load = useCallback(
    async (background = false) => {
      if (controlledHistory !== undefined) return;
      if (!background) setLoading(true);
      try {
        setLoadedHistory(await codebaseApi.listVersions(codebaseId));
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : loadErrorMessage);
      } finally {
        setLoading(false);
      }
    },
    [codebaseId, controlledHistory, loadErrorMessage],
  );

  useEffect(() => {
    if (controlledHistory !== undefined) {
      setLoading(false);
      return;
    }
    void load();
  }, [controlledHistory, load]);

  const activeBuild = history?.active_build;
  useEffect(() => {
    if (!isActiveBuild(activeBuild)) return;
    const timer = window.setInterval(() => {
      if (controlledHistory === undefined) {
        void load(true);
      } else {
        onBuildChanged?.();
      }
    }, BUILD_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeBuild, controlledHistory, load, onBuildChanged]);

  if (loading && !history) {
    return (
      <span role="status" aria-label={t("versionStatusLoading")}>
        <IconLoading className="size-3 animate-spin" />
      </span>
    );
  }

  const active = history?.versions.find((version) => version.is_active) ?? null;
  const historical =
    history?.versions.filter((version) => version.is_published && !version.is_active) ?? [];
  const viewed =
    history?.versions.find((version) => version.id === viewingVersionId) ?? active;
  const capabilities = viewed
    ? Object.entries(viewed.capabilities).map(
        ([name, enabled]) =>
          `${name}: ${enabled ? t("capabilityAvailable") : t("capabilityUnavailable")}`,
      )
    : [];
  const unsupported = unsupportedViews(viewed ?? null);
  const actionableBuild =
    history?.active_build ??
    history?.versions.find((version) => version.build?.can_retry)?.build ??
    null;

  const reloadAfterAction = async () => {
    if (controlledHistory === undefined) {
      await load();
    }
    onBuildChanged?.();
  };

  const runAction = async (action: "retry" | "cancel") => {
    if (!actionableBuild || acting) return;
    setActing(true);
    try {
      if (action === "retry") {
        await codebaseApi.retryBuild(codebaseId, actionableBuild.id);
      } else {
        await codebaseApi.cancelBuild(codebaseId, actionableBuild.id);
      }
      await reloadAfterAction();
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
        <>
          <p className="text-muted-foreground">
            {t("versionCapabilities", {
              capabilities: capabilities.length ? capabilities.join(", ") : t("noCapabilities"),
            })}
          </p>
          {!!viewed.degraded_reasons.length && (
            <p className="text-amber-600 dark:text-amber-500">
              {t("versionDegraded", {
                reasons: viewed.degraded_reasons.join(", "),
              })}
            </p>
          )}
          {!!unsupported.length && (
            <p className="text-muted-foreground">
              {t("unsupportedViews", { views: unsupported.join(", ") })}
            </p>
          )}
        </>
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
    </div>
  );
}
