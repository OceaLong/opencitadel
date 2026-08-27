"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Copy, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  EMPTY_JOB_FORM,
  JobFormSheet,
  jobToFormValues,
} from "@/components/automation/job-form-sheet";
import { JobsTable } from "@/components/automation/jobs-table";
import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { PageHeader } from "@/components/page-header";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { scheduledJobsApi } from "@/lib/api/scheduled-jobs";
import type { CreateScheduledJobParams, ScheduledJob } from "@/lib/api/types";

type WebhookCredentials = {
  jobId: string;
  jobName: string;
  webhookToken: string;
  webhookSecret: string;
};

export default function AutomationPage() {
  const t = useTranslations("automation");
  const tCommon = useTranslations("common");
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [editingJob, setEditingJob] = useState<ScheduledJob | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [rotatingJobId, setRotatingJobId] = useState<string | null>(null);
  const [triggeringJobId, setTriggeringJobId] = useState<string | null>(null);
  const [togglingJobId, setTogglingJobId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ScheduledJob | null>(null);
  const [webhookDialogOpen, setWebhookDialogOpen] = useState(false);
  const [webhookCredentials, setWebhookCredentials] = useState<WebhookCredentials | null>(null);
  const [form, setForm] = useState<CreateScheduledJobParams>(EMPTY_JOB_FORM);

  const closeWebhookDialog = () => {
    setWebhookDialogOpen(false);
    setWebhookCredentials(null);
  };

  const applyWebhookCredentials = (
    job: ScheduledJob,
    webhookToken: string,
    webhookSecret: string,
  ) => setWebhookCredentials({ jobId: job.id, jobName: job.name, webhookToken, webhookSecret });

  const copyText = useCallback(
    async (label: string, value: string) => {
      try {
        await navigator.clipboard.writeText(value);
        toast.success(t("copiedWithLabel", { label }));
      } catch {
        toast.error(tCommon("copyFailed"));
      }
    },
    [t, tCommon],
  );

  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await scheduledJobsApi.list();
      setJobs(data.jobs);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const openSheet = (job: ScheduledJob | null) => {
    setEditingJob(job);
    setForm(job ? jobToFormValues(job) : EMPTY_JOB_FORM);
    setSheetOpen(true);
  };

  const handleSubmit = async () => {
    if (!form.name.trim() || !form.prompt_template.trim()) {
      toast.error(t("nameRequired"));
      return;
    }
    if (form.operator_scope && !form.operator_domains?.length) {
      toast.error(t("operatorDomainsRequired"));
      return;
    }
    setCreating(true);
    try {
      if (editingJob) {
        const updated = await scheduledJobsApi.update(editingJob.id, form);
        setJobs((prev) => prev.map((job) => (job.id === updated.id ? updated : job)));
        setSheetOpen(false);
        toast.success(t("jobUpdated"));
        return;
      }
      const result = await scheduledJobsApi.create(form);
      setJobs((prev) => [result.job, ...prev]);
      setSheetOpen(false);
      if (result.webhook_secret && result.job.webhook_token) {
        applyWebhookCredentials(result.job, result.job.webhook_token, result.webhook_secret);
      } else {
        toast.success(t("jobCreated"));
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : editingJob
            ? t("updateFailed")
            : t("jobCreateFailed"),
      );
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!pendingDelete) return;
    try {
      await scheduledJobsApi.delete(pendingDelete.id);
      setJobs((prev) => prev.filter((job) => job.id !== pendingDelete.id));
      toast.success(t("jobDeleted"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("deleteFailed"));
    } finally {
      setPendingDelete(null);
    }
  };

  const handleRotateSecret = async (job: ScheduledJob) => {
    setRotatingJobId(job.id);
    try {
      const result = await scheduledJobsApi.rotateSecret(job.id);
      applyWebhookCredentials(job, result.webhook_token, result.webhook_secret);
      setJobs((prev) =>
        prev.map((item) =>
          item.id === job.id ? { ...item, webhook_token: result.webhook_token } : item,
        ),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("rotateSecretFailed"));
    } finally {
      setRotatingJobId(null);
    }
  };

  const handleToggleEnabled = async (job: ScheduledJob, enabled: boolean) => {
    setTogglingJobId(job.id);
    try {
      await scheduledJobsApi.update(job.id, { enabled });
      await loadJobs();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("toggleEnabledFailed"));
    } finally {
      setTogglingJobId(null);
    }
  };

  const handleRunNow = async (job: ScheduledJob) => {
    setTriggeringJobId(job.id);
    try {
      const result = await scheduledJobsApi.trigger(job.id);
      toast.success(t("runNowStarted"));
      await loadJobs();
      if (result.session_id) {
        window.location.href = `/sessions/${result.session_id}`;
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("runNowFailed"));
    } finally {
      setTriggeringJobId(null);
    }
  };

  const webhookUrl = (token: string) =>
    `${typeof window !== "undefined" ? window.location.origin : ""}/api/webhooks/${token}`;

  return (
    <ScrollablePageContent>
      <PageHeader
        title={t("title")}
        description={t("subtitle")}
        actions={
          <>
            <Button variant="outline" onClick={() => void loadJobs()}>
              <RefreshCw className="size-4" />
              {t("refresh")}
            </Button>
            <Button onClick={() => openSheet(null)}>
              <Plus className="size-4" />
              {t("newJob")}
            </Button>
          </>
        }
      />

      <Dialog
        open={webhookDialogOpen || webhookCredentials != null}
        onOpenChange={(open) => !open && closeWebhookDialog()}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("webhookCredentialsTitle")}</DialogTitle>
            <DialogDescription>
              {t("webhookCredentialsDescription")}{" "}
              <code className="text-xs">X-Webhook-Signature: HMAC-SHA256(body, secret)</code>
            </DialogDescription>
          </DialogHeader>
          {webhookCredentials && (
            <div className="space-y-3 text-sm">
              {(
                [
                  [
                    "webhookUrlLabel",
                    webhookUrl(webhookCredentials.webhookToken),
                    "copyWebhookUrlAria",
                  ],
                  ["tokenLabel", webhookCredentials.webhookToken, "copyTokenAria"],
                  ["secretLabel", webhookCredentials.webhookSecret, "copySecretAria"],
                ] as const
              ).map(([labelKey, value, ariaKey]) => (
                <div key={labelKey} className="space-y-1">
                  <Label>{t(labelKey)}</Label>
                  <div className="flex gap-2">
                    <Input readOnly value={value} />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={t(ariaKey)}
                      onClick={() => void copyText(t(labelKey), value)}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button onClick={closeWebhookDialog}>{t("credentialsSaved")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <JobFormSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        editingJob={editingJob}
        form={form}
        onFormChange={setForm}
        onSubmit={() => void handleSubmit()}
        submitting={creating}
      />

      <JobsTable
        jobs={jobs}
        loading={loading}
        onNewJob={() => openSheet(null)}
        onEdit={openSheet}
        onDelete={setPendingDelete}
        onToggle={(job, enabled) => void handleToggleEnabled(job, enabled)}
        onRunNow={(job) => void handleRunNow(job)}
        onRotateSecret={(job) => void handleRotateSecret(job)}
        webhookUrl={webhookUrl}
        togglingJobId={togglingJobId}
        triggeringJobId={triggeringJobId}
        rotatingJobId={rotatingJobId}
      />

      <ConfirmDeleteDialog
        open={pendingDelete != null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title={t("deleteJobTitle")}
        description={t("deleteJobDescription", { name: pendingDelete?.name ?? "" })}
        onConfirm={handleDelete}
      />
    </ScrollablePageContent>
  );
}
