"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Loader2, Plus, RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { ContextSelector } from "@/components/context-selector";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { StatusBadge } from "@/components/status-badge";
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
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [rotatingJobId, setRotatingJobId] = useState<string | null>(null);
  const [triggeringJobId, setTriggeringJobId] = useState<string | null>(null);
  const [togglingJobId, setTogglingJobId] = useState<string | null>(null);
  const [webhookCredentials, setWebhookCredentials] = useState<WebhookCredentials | null>(null);
  const [form, setForm] = useState<CreateScheduledJobParams>({
    name: "",
    trigger_type: "interval",
    trigger_spec: "3600",
    prompt_template: "",
    enabled: true,
    notify_channels: [],
    operator_domains: [],
  });

  const resetForm = () => {
    setForm({
      name: "",
      trigger_type: "interval",
      trigger_spec: "3600",
      prompt_template: "",
      enabled: true,
      notify_channels: [],
      operator_domains: [],
    });
    setEditingJobId(null);
  };

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
      if (editingJobId) {
        const updated = await scheduledJobsApi.update(editingJobId, form);
        setJobs((prev) => prev.map((job) => (job.id === updated.id ? updated : job)));
        setShowForm(false);
        resetForm();
        toast.success(t("jobUpdated"));
        return;
      }
      const result = await scheduledJobsApi.create(form);
      setJobs((prev) => [result.job, ...prev]);
      setShowForm(false);
      resetForm();
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
      toast.error(
        error instanceof Error
          ? error.message
          : editingJobId
            ? t("updateFailed")
            : t("jobCreateFailed"),
      );
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
    <div className="flex h-full min-h-screen flex-col">
      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6">
        <PageHeader
          bordered={false}
          size="md"
          title={t("title")}
          description={t("subtitle")}
          actions={
            <Button onClick={() => setShowForm((value) => !value)}>
              <Plus className="size-4" />
              {t("newJob")}
            </Button>
          }
        />

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
              <CardTitle className="text-base">
                {editingJobId ? t("editJob") : t("newJob")}
              </CardTitle>
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
                  {form.trigger_type === "cron" && (
                    <p className="text-muted-foreground text-xs">{t("cronHelp")}</p>
                  )}
                  {form.trigger_type === "interval" && (
                    <p className="text-muted-foreground text-xs">{t("triggerTypeInterval")}</p>
                  )}
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
                <p className="text-muted-foreground text-xs">{t("promptHelp")}</p>
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
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("fields.modelId")}</Label>
                  <SessionModelPicker
                    value={form.model_id ?? undefined}
                    onChange={(id) => setForm((prev) => ({ ...prev, model_id: id ?? null }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("fields.skillId")}</Label>
                  <SessionSkillPicker
                    value={form.skill_id ?? undefined}
                    onChange={(id) => setForm((prev) => ({ ...prev, skill_id: id ?? null }))}
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="notify-server">{t("fields.notifyServer")}</Label>
                  <Input
                    id="notify-server"
                    value={form.notify_channels?.[0]?.server_name ?? ""}
                    onChange={(event) =>
                      setForm((prev) => ({
                        ...prev,
                        notify_channels: event.target.value
                          ? [{ type: "mcp", server_name: event.target.value, channel_arg: prev.notify_channels?.[0]?.channel_arg ?? "" }]
                          : [],
                      }))
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="notify-channel">{t("fields.notifyChannel")}</Label>
                  <Input
                    id="notify-channel"
                    value={form.notify_channels?.[0]?.channel_arg ?? ""}
                    onChange={(event) =>
                      setForm((prev) => ({
                        ...prev,
                        notify_channels: prev.notify_channels?.[0]?.server_name
                          ? [{ ...prev.notify_channels[0], channel_arg: event.target.value }]
                          : [],
                      }))
                    }
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label>{t("fields.operatorScope")}</Label>
                  <Select
                    value={form.operator_scope ?? "none"}
                    onValueChange={(value) =>
                      setForm((prev) => ({
                        ...prev,
                        operator_scope: value === "none" ? null : (value as "owned" | "third_party_saas"),
                      }))
                    }
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">—</SelectItem>
                      <SelectItem value="owned">owned</SelectItem>
                      <SelectItem value="third_party_saas">third_party_saas</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="operator-domains">{t("fields.operatorDomains")}</Label>
                  <Input
                    id="operator-domains"
                    value={(form.operator_domains ?? []).join(", ")}
                    onChange={(event) =>
                      setForm((prev) => ({
                        ...prev,
                        operator_domains: event.target.value.split(/[,\n]+/).map((s) => s.trim()).filter(Boolean),
                      }))
                    }
                    placeholder="ops-console"
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label>{t("fields.gateProfile")}</Label>
                <Select
                  value={form.gate_profile ?? "standard"}
                  onValueChange={(value) =>
                    setForm((prev) => ({
                      ...prev,
                      gate_profile: value as CreateScheduledJobParams["gate_profile"],
                    }))
                  }
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="loose">loose</SelectItem>
                    <SelectItem value="standard">standard</SelectItem>
                    <SelectItem value="strict">strict</SelectItem>
                  </SelectContent>
                </Select>
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
                  {creating ? t("creating") : editingJobId ? t("saveJob") : t("create")}
                </Button>
                <Button variant="ghost" onClick={() => { setShowForm(false); resetForm(); }}>
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
                      <Switch
                        checked={job.enabled}
                        disabled={togglingJobId === job.id}
                        onCheckedChange={(checked) => void handleToggleEnabled(job, checked)}
                        aria-label={job.enabled ? t("statusEnabled") : t("statusDisabled")}
                      />
                      {job.last_run_status ? (
                        <StatusBadge
                          variant={
                            job.last_run_status === "failed"
                              ? "destructive"
                              : job.last_run_status === "running"
                                ? "warning"
                                : job.last_run_status === "success" ||
                                    job.last_run_status === "completed"
                                  ? "success"
                                  : "secondary"
                          }
                        >
                          {job.last_run_status}
                        </StatusBadge>
                      ) : null}
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
                    {job.last_run_error && (
                      <p className="text-destructive text-xs">
                        {t("lastRunError", { error: job.last_run_error })}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    {job.trigger_type !== "webhook" && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!job.enabled || triggeringJobId === job.id}
                        onClick={() => void handleRunNow(job)}
                      >
                        {triggeringJobId === job.id ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          t("runNow")
                        )}
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditingJobId(job.id);
                        setForm({
                          name: job.name,
                          trigger_type: job.trigger_type as CreateScheduledJobParams["trigger_type"],
                          trigger_spec: job.trigger_spec,
                          prompt_template: job.prompt_template,
                          skill_id: job.skill_id,
                          model_id: job.model_id,
                          codebase_id: job.codebase_id,
                          knowledge_base_id: job.knowledge_base_id,
                          notify_channels: job.notify_channels ?? [],
                          operator_scope: job.operator_scope ?? null,
                          operator_domains: job.operator_domains ?? [],
                          gate_profile: job.gate_profile ?? "standard",
                          enabled: job.enabled,
                        });
                        setShowForm(true);
                      }}
                    >
                      {t("editJob")}
                    </Button>
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
