"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";

import { scheduledJobsApi } from "@/lib/api/scheduled-jobs";
import type { ScheduledJobRun } from "@/lib/api/types";

const PAGE_SIZE = 20;

function formatTime(value: string | null | undefined, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale);
}

function runVariant(status: string): "destructive" | "warning" | "success" | "secondary" {
  if (status === "failed") return "destructive";
  if (status === "running") return "warning";
  if (status === "success" || status === "completed") return "success";
  return "secondary";
}

type JobRunsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string | null;
  jobName: string;
};

/**
 * 定时任务运行历史对话框：经 `GET /scheduled-jobs/{job_id}/runs` 分页拉取，
 * 展示每次运行的状态/时间/错误。
 */
export function JobRunsDialog({ open, onOpenChange, jobId, jobName }: JobRunsDialogProps) {
  const t = useTranslations("automation");
  const locale = useLocale();
  const [runs, setRuns] = useState<ScheduledJobRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const loadPage = useCallback(
    async (offset: number) => {
      if (!jobId) return;
      setLoading(true);
      setError(null);
      try {
        const data = await scheduledJobsApi.listRuns(jobId, { limit: PAGE_SIZE, offset });
        setRuns((prev) => (offset === 0 ? data.runs : [...prev, ...data.runs]));
        setHasMore(data.runs.length === PAGE_SIZE);
      } catch (err) {
        setError(err instanceof Error ? err.message : t("runsLoadFailed"));
      } finally {
        setLoading(false);
      }
    },
    [jobId, t],
  );

  // Depend only on open/jobId and read loadPage via a ref: depending on
  // loadPage directly would re-run this effect on every render when the
  // injected `t` identity is unstable, spinning into an infinite render loop.
  const loadPageRef = useRef(loadPage);
  useEffect(() => {
    loadPageRef.current = loadPage;
  }, [loadPage]);

  useEffect(() => {
    if (open && jobId) {
      setRuns([]);
      setHasMore(false);
      void loadPageRef.current(0);
    }
  }, [open, jobId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{t("runsTitle")}</DialogTitle>
          <DialogDescription>{t("runsSubtitle", { name: jobName })}</DialogDescription>
        </DialogHeader>

        {error ? (
          <div className="text-destructive py-8 text-center text-sm">{error}</div>
        ) : loading && runs.length === 0 ? (
          <div className="text-muted-foreground flex items-center justify-center gap-2 py-10">
            <Loader2 className="size-4 animate-spin" />
            {t("runsLoading")}
          </div>
        ) : runs.length === 0 ? (
          <EmptyState title={t("runsEmpty")} className="py-10" />
        ) : (
          <ScrollArea className="max-h-[60vh]">
            <ul className="space-y-2 pr-2">
              {runs.map((run) => (
                <li key={run.run_id} className="rounded-md border px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <StatusBadge variant={runVariant(run.status)}>{run.status}</StatusBadge>
                    <span className="text-muted-foreground text-xs">{run.family}</span>
                  </div>
                  <div className="text-muted-foreground mt-1 space-y-0.5 text-xs">
                    <p>{t("runStartedAt", { time: formatTime(run.started_at, locale) })}</p>
                    <p>{t("runFinishedAt", { time: formatTime(run.finished_at, locale) })}</p>
                  </div>
                  {run.error ? (
                    <p className="text-destructive mt-1 text-xs">
                      {t("runError", { error: run.error })}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
            {hasMore ? (
              <div className="pt-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={loading}
                  onClick={() => void loadPage(runs.length)}
                >
                  {loading ? <Loader2 className="size-4 animate-spin" /> : t("runsLoadMore")}
                </Button>
              </div>
            ) : null}
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}
