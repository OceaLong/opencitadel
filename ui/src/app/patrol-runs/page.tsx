"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { RefreshCw } from "lucide-react";

import { AsyncBoundary } from "@/components/async-boundary";
import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { type PaginatedFetcher, usePaginatedList } from "@/hooks/use-paginated-list";
import { patrolStatusVariant, usePatrolLabels } from "@/hooks/use-patrol-labels";
import { usePatrolPacks } from "@/hooks/use-patrol-packs";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolRun } from "@/lib/api/types";
import { toBcp47 } from "@/lib/utils";

const RUN_STATUSES = [
  "queued",
  "running",
  "completed",
  "completed_with_findings",
  "failed",
  "cancelled",
] as const;

/**
 * 跨包巡检运行历史（GET /api/patrol-runs）。pack 名称来自 AppShell 挂载的
 * PatrolPacksProvider（/patrol-runs 属于 patrol 模块，Provider 已启用拉取）。
 */
export default function PatrolRunsPage() {
  const t = useTranslations("patrol");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const labels = usePatrolLabels();
  const { packs } = usePatrolPacks();
  const packNames = useMemo(
    () => Object.fromEntries(packs.map((pack) => [pack.id, pack.name])),
    [packs],
  );
  const [status, setStatus] = useState<string>("all");

  // /api/patrol-runs 不返回 total：多取一条判断是否还有下一页。
  const fetchRuns = useCallback<PaginatedFetcher<PatrolRun>>(
    async ({ limit, offset }) => {
      const data = await patrolsApi.listRuns({
        status: status === "all" ? undefined : status,
        limit: limit + 1,
        offset,
      });
      const hasMore = data.items.length > limit;
      const items = hasMore ? data.items.slice(0, limit) : data.items;
      return { items, total: offset + items.length + (hasMore ? 1 : 0) };
    },
    [status],
  );

  const { items, loading, error, offset, canPrev, canNext, load, nextPage, prevPage } =
    usePaginatedList<PatrolRun>(fetchRuns);

  useEffect(() => {
    // status 筛选变化时回到第一页重新加载。
    void load(0);
  }, [fetchRuns, load]);

  return (
    <ScrollablePageContent>
      <div className="grid gap-5">
        <PageHeader
          title={t("runs.title")}
          description={t("runs.description")}
          actions={
            <>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="w-full sm:w-[200px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("runs.filterAll")}</SelectItem>
                  {RUN_STATUSES.map((item) => (
                    <SelectItem key={item} value={item}>
                      {labels.status[item] ?? item}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" onClick={() => void load(offset)}>
                <RefreshCw className="size-4" />
                {t("actions.refresh")}
              </Button>
            </>
          }
        />
        <AsyncBoundary
          loading={loading}
          error={error}
          onRetry={() => void load(offset)}
          loadingFallback={
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          }
        >
          {items.length === 0 ? (
            <EmptyState title={t("empty.noRuns")} className="py-12" />
          ) : (
            <div className="grid gap-2">
              {items.map((run) => (
                <Link
                  href={`/patrol-runs/${run.id}`}
                  key={run.id}
                  className="hover:bg-muted/50 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3"
                >
                  <span className="min-w-0 text-sm">
                    <span className="font-medium">{packNames[run.pack_id] ?? run.pack_id}</span> ·{" "}
                    {new Date(run.created_at).toLocaleString(toBcp47(locale))} ·{" "}
                    <span translate="no">v{run.pack_version}</span> ·{" "}
                    <span className="text-muted-foreground">
                      {labels.trigger[run.trigger_type] ?? run.trigger_type}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <StatusBadge variant={patrolStatusVariant(run.status)}>
                      {labels.status[run.status] ?? run.status}
                    </StatusBadge>
                    <span className="text-muted-foreground text-xs">
                      {t("status.pass")} {run.counts.pass} / {t("labels.finding")}{" "}
                      {run.counts.warn + run.counts.fail + run.counts.error}
                    </span>
                  </span>
                </Link>
              ))}
            </div>
          )}
          {(canPrev || canNext) && (
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!canPrev}
                onClick={() => void prevPage()}
              >
                {tCommon("previousPage")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!canNext}
                onClick={() => void nextPage()}
              >
                {tCommon("nextPage")}
              </Button>
            </div>
          )}
        </AsyncBoundary>
      </div>
    </ScrollablePageContent>
  );
}
