"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Loader2, Plus, RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { ChatHeader } from "@/components/chat-header";
import { ContextSelector } from "@/components/context-selector";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { scheduledJobsApi } from "@/lib/api/scheduled-jobs";
import type { CreateScheduledJobParams, ScheduledJob } from "@/lib/api/types";
import { IconDelete } from "@/lib/icons";

const TRIGGER_LABEL: Record<string, string> = {
  interval: "triggerInterval",
  cron: "triggerCron",
  webhook: "triggerWebhook",
};

type WebhookCredentials = {
  jobId: string;
  jobName: string;
  webhookToken: string;
  webhookSecret: string;
};

function formatTime(value: string | null | undefined, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale);
}

export default function AutomationPage() {
  const locale = useLocale();
  const t = useTranslations("automation");
  const tCommon = useTranslations("common");
  const tSessionMemory = useTranslations("sessionMemory");
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [rotatingJobId, setRotatingJobId] = useState<string | null>(null);
  const [webhookCredentials, setWebhookCredentials] = useState<WebhookCredentials | null>(null);
  const [form, setForm] = useState<CreateScheduledJobParams>({
    name: "",
    trigger_type: "interval",
    trigger_spec: "3600",
    prompt_template: "",
    enabled: true,
  });

  const copyText = useCallback(
    async (label: string, value: string) => {
      try {
        await navigator.clipboard.writeText(value);
        toast.success(t("copiedWithLabel", { label }));
      } catch {
        toast.error(tSessionMemory("copyFailed"));
      }
    },
    [t, tSessionMemory],
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

  const handleCreate = async () => {
    if (!form.name.trim() || !form.prompt_template.trim()) {
      toast.error(t("nameRequired"));
      return;
    }
    setCreating(true);
    try {
      const result = await scheduledJobsApi.create(form);
      setJobs((prev) => [result.job, ...prev]);
      setShowForm(false);
      setForm({
        name: "",
        trigger_type: "interval",
        trigger_spec: "3600",
        prompt_template: "",
        enabled: true,
      });
      if (result.webhook_secret && result.job.webhook_token) {
        setWebhookCredentials({
          jobId: result.job.id,
          jobName: result.job.name,
          webhookToken: result.job.webhook_token,
          webhookSecret: result.webhook_secret,
        });
      } else {
        toast.success(t("jobCreated"));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("jobCreateFailed"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (jobId: string) => {
    try {
      await scheduledJobsApi.delete(jobId);
      setJobs((prev) => prev.filter((job) => job.id !== jobId));
      toast.success(t("jobDeleted"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("deleteFailed"));
    }
  };

  const handleRotateSecret = async (job: ScheduledJob) => {
    setRotatingJobId(job.id);
    try {
      const result = await scheduledJobsApi.rotateSecret(job.id);
      setWebhookCredentials({
        jobId: job.id,
        jobName: job.name,
        webhookToken: result.webhook_token,
        webhookSecret: result.webhook_secret,
      });
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

  const webhookUrl = (token: string) =>
    `${typeof window !== "undefined" ? window.location.origin : ""}/api/webhooks/${token}`;

  return (
    <div className="flex h-full min-h-screen flex-col">
      <ChatHeader />
      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-foreground text-xl font-semibold">{t("title")}</h1>
            <p className="text-muted-foreground text-sm">{t("subtitle")}</p>
          </div>
          <Button onClick={() => setShowForm((value) => !value)}>
            <Plus className="size-4" />
            {t("newJob")}
          </Button>
        </div>

        <Dialog open={webhookCredentials != null} onOpenChange={(open) => !open && setWebhookCredentials(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("webhookCredentialsTitle")}</DialogTitle>
              <DialogDescription>
                {t("webhookCredentialsDescription")}{" "}
                <code className="text-xs">X-Webhook-Signature: HMAC-SHA256(body, secret)</code>
              </DialogDescription>
            </DialogHeader>
            {webhookCredentials && (
              <div className="space-y-3 text-sm">
                <div className="space-y-1">
                  <Label>Webhook URL</Label>
                  <div className="flex gap-2">
                    <Input readOnly value={webhookUrl(webhookCredentials.webhookToken)} />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={t("copyWebhookUrlAria")}
                      onClick={() => void copyText("Webhook URL", webhookUrl(webhookCredentials.webhookToken))}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </div>
                <div className="space-y-1">
                  <Label>Token</Label>
                  <div className="flex gap-2">
                    <Input readOnly value={webhookCredentials.webhookToken} />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={t("copyTokenAria")}
                      onClick={() => void copyText("Token", webhookCredentials.webhookToken)}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </div>
                <div className="space-y-1">
                  <Label>Secret</Label>
                  <div className="flex gap-2">
                    <Input readOnly value={webhookCredentials.webhookSecret} />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={t("copySecretAria")}
                      onClick={() => void copyText("Secret", webhookCredentials.webhookSecret)}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </div>
              </div>
            )}
            <DialogFooter>
              <Button onClick={() => setWebhookCredentials(null)}>{t("credentialsSaved")}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {showForm && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("newJob")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="job-name">{t("fields.name")}</Label>
                <Input
                  id="job-name"
                  value={form.name}
                  onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                  placeholder={t("namePlaceholder")}
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("fields.triggerType")}</Label>
                  <Select
                    value={form.trigger_type}
                    onValueChange={(value) =>
                      setForm((prev) => ({
                        ...prev,
                        trigger_type: value as CreateScheduledJobParams["trigger_type"],
                      }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="interval">{t("triggerTypeInterval")}</SelectItem>
                      <SelectItem value="cron">Cron</SelectItem>
                      <SelectItem value="webhook">Webhook</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>{t(TRIGGER_LABEL[form.trigger_type ?? "interval"])}</Label>
                  <Input
                    value={form.trigger_spec}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, trigger_spec: event.target.value }))
                    }
                    placeholder={form.trigger_type === "cron" ? "0 9 * * *" : "3600"}
                    disabled={form.trigger_type === "webhook"}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="job-prompt">{t("fields.prompt")}</Label>
                <Textarea
                  id="job-prompt"
                  rows={4}
                  value={form.prompt_template}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, prompt_template: event.target.value }))
                  }
                  placeholder={t("promptPlaceholder")}
                />
              </div>
              <div className="space-y-2">
                <Label>{t("contextLabel")}</Label>
                <ContextSelector
                  value={{
                    codebaseId: form.codebase_id ?? undefined,
                    knowledgeBaseId: form.knowledge_base_id ?? undefined,
                  }}
                  onChange={(ctx) =>
                    setForm((prev) => ({
                      ...prev,
                      codebase_id: ctx.codebaseId ?? null,
                      knowledge_base_id: ctx.knowledgeBaseId ?? null,
                    }))
                  }
                />
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={form.enabled ?? true}
                  onCheckedChange={(checked) => setForm((prev) => ({ ...prev, enabled: checked }))}
                />
                <Label>{t("fields.enabled")}</Label>
              </div>
              <div className="flex gap-2">
                <Button onClick={() => void handleCreate()} disabled={creating}>
                  {creating ? t("creating") : t("create")}
                </Button>
                <Button variant="ghost" onClick={() => setShowForm(false)}>
                  {tCommon("cancel")}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {loading ? (
          <div className="text-muted-foreground flex items-center justify-center gap-2 py-12">
            <Loader2 className="size-4 animate-spin" />
            {tCommon("loading")}
          </div>
        ) : jobs.length === 0 ? (
          <p className="text-muted-foreground py-12 text-center text-sm">{t("empty")}</p>
        ) : (
          <div className="space-y-3">
            {jobs.map((job) => (
              <Card key={job.id}>
                <CardContent className="flex flex-wrap items-start justify-between gap-4 py-4">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-foreground font-medium">{job.name}</p>
                      <span
                        className={
                          job.enabled
                            ? "text-emerald-600 text-xs"
                            : "text-muted-foreground text-xs"
                        }
                      >
                        {job.enabled ? t("statusEnabled") : t("statusDisabled")}
                      </span>
                    </div>
                    <p className="text-muted-foreground line-clamp-2 text-sm">{job.prompt_template}</p>
                    <p className="text-muted-foreground text-xs">
                      {job.trigger_type} · {job.trigger_spec}
                      {job.next_run_at ? ` · ${t("nextRunAt", { time: formatTime(job.next_run_at, locale) })}` : ""}
                    </p>
                    {job.trigger_type === "webhook" && job.webhook_token && (
                      <p className="text-muted-foreground break-all text-xs">
                        Webhook: {webhookUrl(job.webhook_token)}
                      </p>
                    )}
                    {job.last_run_at && (
                      <p className="text-muted-foreground text-xs">
                        {t("lastRunAt", { time: formatTime(job.last_run_at, locale) })}
                        {job.last_run_status ? ` · ${job.last_run_status}` : ""}
                        {job.last_run_session_id ? (
                          <>
                            {" · "}
                            <a
                              href={`/sessions/${job.last_run_session_id}`}
                              className="text-primary underline"
                            >
                              {t("viewSession")}
                            </a>
                          </>
                        ) : null}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {job.trigger_type === "webhook" && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("rotateSecretAria")}
                        disabled={rotatingJobId === job.id}
                        onClick={() => void handleRotateSecret(job)}
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
                      onClick={() => void handleDelete(job.id)}
                    >
                      <IconDelete className="size-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
