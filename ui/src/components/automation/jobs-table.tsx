"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { Loader2, Plus, RefreshCw } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { ScheduledJob } from "@/lib/api/types";
import { IconDelete } from "@/lib/icons";

function formatTime(value: string | null | undefined, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale);
}

function lastRunVariant(status: string): "destructive" | "warning" | "success" | "secondary" {
  if (status === "failed") return "destructive";
  if (status === "running") return "warning";
  if (status === "success" || status === "completed") return "success";
  return "secondary";
}

type JobsTableProps = {
  jobs: ScheduledJob[];
  loading: boolean;
  onNewJob: () => void;
  onEdit: (job: ScheduledJob) => void;
  onDelete: (job: ScheduledJob) => void;
  onToggle: (job: ScheduledJob, enabled: boolean) => void;
  onRunNow: (job: ScheduledJob) => void;
  onViewRuns: (job: ScheduledJob) => void;
  onRotateSecret: (job: ScheduledJob) => void;
  webhookUrl: (token: string) => string;
  togglingJobId: string | null;
  triggeringJobId: string | null;
  rotatingJobId: string | null;
};

export function JobsTable({
  jobs,
  loading,
  onNewJob,
  onEdit,
  onDelete,
  onToggle,
  onRunNow,
  onViewRuns,
  onRotateSecret,
  webhookUrl,
  togglingJobId,
  triggeringJobId,
  rotatingJobId,
}: JobsTableProps) {
  const t = useTranslations("automation");
  const tCommon = useTranslations("common");
  const locale = useLocale();

  if (loading) {
    return (
      <div className="text-muted-foreground flex items-center justify-center gap-2 py-12">
        <Loader2 className="size-4 animate-spin" />
        {tCommon("loading")}
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <EmptyState
        variant="dashed"
        title={t("empty")}
        action={
          <Button onClick={onNewJob}>
            <Plus className="size-4" />
            {t("newJob")}
          </Button>
        }
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{tCommon("name")}</TableHead>
          <TableHead>{t("columnTrigger")}</TableHead>
          <TableHead>{t("lastRunAt")}</TableHead>
          <TableHead>{t("nextRunAt")}</TableHead>
          <TableHead className="text-right">{t("columnActions")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => (
          <TableRow key={job.id}>
            <TableCell className="max-w-xs min-w-48 align-top whitespace-normal">
              <div className="flex flex-wrap items-center gap-2">
                <Switch
                  checked={job.enabled}
                  disabled={togglingJobId === job.id}
                  onCheckedChange={(checked) => onToggle(job, checked)}
                  aria-label={job.enabled ? t("statusEnabled") : t("statusDisabled")}
                />
                <span className="text-foreground font-medium">{job.name}</span>
              </div>
              <p className="text-muted-foreground line-clamp-1 text-sm">{job.prompt_template}</p>
            </TableCell>
            <TableCell className="max-w-56 min-w-40 align-top whitespace-normal">
              <p>
                {job.trigger_type} · {job.trigger_spec}
              </p>
              {job.trigger_type === "webhook" && job.webhook_token && (
                <p className="text-muted-foreground font-mono text-xs break-all">
                  {webhookUrl(job.webhook_token)}
                </p>
              )}
            </TableCell>
            <TableCell className="min-w-40 align-top whitespace-normal">
              <p className="font-mono text-xs">{formatTime(job.last_run_at, locale)}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {job.last_run_status ? (
                  <StatusBadge variant={lastRunVariant(job.last_run_status)}>
                    {job.last_run_status}
                  </StatusBadge>
                ) : null}
                {job.last_run_session_id ? (
                  <Link
                    href={`/sessions/${job.last_run_session_id}`}
                    className="text-primary text-xs underline"
                  >
                    {t("viewSession")}
                  </Link>
                ) : null}
              </div>
              {job.last_run_error && (
                <p className="text-destructive mt-1 text-xs">
                  {t("lastRunError", { error: job.last_run_error })}
                </p>
              )}
            </TableCell>
            <TableCell className="align-top font-mono text-xs">
              {formatTime(job.next_run_at, locale)}
            </TableCell>
            <TableCell className="align-top">
              <div className="flex flex-wrap items-center justify-end gap-1">
                {job.trigger_type !== "webhook" && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!job.enabled || triggeringJobId === job.id}
                    onClick={() => onRunNow(job)}
                  >
                    {triggeringJobId === job.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      t("runNow")
                    )}
                  </Button>
                )}
                <Button variant="ghost" size="sm" onClick={() => onViewRuns(job)}>
                  {t("runsAction")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => onEdit(job)}>
                  {t("editJob")}
                </Button>
                {job.trigger_type === "webhook" && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t("rotateSecretAria")}
                    disabled={rotatingJobId === job.id}
                    onClick={() => onRotateSecret(job)}
                  >
                    {rotatingJobId === job.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <RefreshCw className="size-4" />
                    )}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("deleteJobAria")}
                  onClick={() => onDelete(job)}
                >
                  <IconDelete className="size-4" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
