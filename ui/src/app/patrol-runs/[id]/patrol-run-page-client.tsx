"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { AsyncBoundary } from "@/components/async-boundary";
import { PatrolRunDetailView } from "@/components/patrol/patrol-run-detail";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { Skeleton } from "@/components/ui/skeleton";

import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolRunDetail } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";
import { useReportPageTitle } from "@/providers/page-title-provider";

export function PatrolRunPageClient({ id }: { id: string }) {
  const t = useTranslations("patrol");
  const { user } = useAuth();
  const [run, setRun] = useState<PatrolRunDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 重试计数：错误态点"重试"时递增以重启轮询 effect。
  const [reloadKey, setReloadKey] = useState(0);
  // 首屏是否已成功加载过：加载后轮询失败只 toast，不整页替换为错误态。
  const loadedRef = useRef(false);
  useReportPageTitle(
    run ? `Run #${run.id.length > 8 ? `${run.id.slice(0, 8)}…` : run.id}` : undefined,
  );
  const load = useCallback(async () => {
    try {
      setRun(await patrolsApi.getRun(id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.runLoad"));
    }
  }, [id, t]);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await patrolsApi.getRun(id);
        if (!active) return;
        loadedRef.current = true;
        setRun(next);
        setLoadError(null);
        if (["queued", "running"].includes(next.status)) {
          timer = window.setTimeout(() => void poll(), 3000);
        }
      } catch (error) {
        if (!active) return;
        const message = error instanceof Error ? error.message : t("errors.runLoad");
        if (loadedRef.current) toast.error(message);
        else setLoadError(message);
      }
    };
    timer = window.setTimeout(() => void poll(), 0);
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [id, t, reloadKey]);
  if (!run)
    return (
      <ScrollablePageContent>
        <AsyncBoundary
          loading={!loadError}
          error={loadError}
          onRetry={() => {
            setLoadError(null);
            setReloadKey((key) => key + 1);
          }}
          loadingFallback={
            <div className="grid gap-3">
              <Skeleton className="h-44" />
              <Skeleton className="h-72" />
            </div>
          }
        >
          {null}
        </AsyncBoundary>
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <PatrolRunDetailView
        run={run}
        readOnly={user?.global_role === "auditor"}
        onRefresh={() => void load()}
      />
    </ScrollablePageContent>
  );
}
