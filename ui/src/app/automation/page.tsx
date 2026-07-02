"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ChatHeader } from "@/components/chat-header";
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

const TRIGGER_LABEL: Record<string, string> = {
  interval: "间隔（秒）",
  cron: "Cron 表达式",
  webhook: "Webhook",
};

type WebhookCredentials = {
  jobId: string;
  jobName: string;
  webhookToken: string;
  webhookSecret: string;
};

function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

async function copyText(label: string, value: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} 已复制`);
  } catch {
    toast.error("复制失败");
  }
}

export default function AutomationPage() {
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

  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await scheduledJobsApi.list();
      setJobs(data.jobs);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载任务失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const handleCreate = async () => {
    if (!form.name.trim() || !form.prompt_template.trim()) {
      toast.error("请填写任务名称和提示词模板");
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
        toast.success("任务已创建");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建任务失败");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (jobId: string) => {
    try {
      await scheduledJobsApi.delete(jobId);
      setJobs((prev) => prev.filter((job) => job.id !== jobId));
      toast.success("任务已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
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
      toast.error(error instanceof Error ? error.message : "轮换密钥失败");
    } finally {
      setRotatingJobId(null);
    }
  };

  const webhookUrl = (token: string) =>
    `${typeof window !== "undefined" ? window.location.origin : ""}/api/webhooks/${token}`;

  return (
    <div className="flex h-full min-h-screen flex-col">
      <ChatHeader showSidebarTrigger={false} />
      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-foreground text-xl font-semibold">自动化任务</h1>
            <p className="text-muted-foreground text-sm">定时或 Webhook 触发 Agent 任务</p>
          </div>
          <Button onClick={() => setShowForm((value) => !value)}>
            <Plus className="size-4" />
            新建任务
          </Button>
        </div>

        <Dialog open={webhookCredentials != null} onOpenChange={(open) => !open && setWebhookCredentials(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Webhook 凭证（仅显示一次）</DialogTitle>
              <DialogDescription>
                请立即保存密钥。请求须携带 Header{" "}
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
                      aria-label="复制 Webhook URL"
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
                      aria-label="复制 Token"
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
                      aria-label="复制 Secret"
                      onClick={() => void copyText("Secret", webhookCredentials.webhookSecret)}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </div>
              </div>
            )}
            <DialogFooter>
              <Button onClick={() => setWebhookCredentials(null)}>我已保存</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {showForm && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">新建自动化任务</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="job-name">任务名称</Label>
                <Input
                  id="job-name"
                  value={form.name}
                  onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                  placeholder="例如：每日摘要"
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>触发类型</Label>
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
                      <SelectItem value="interval">间隔</SelectItem>
                      <SelectItem value="cron">Cron</SelectItem>
                      <SelectItem value="webhook">Webhook</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>{TRIGGER_LABEL[form.trigger_type ?? "interval"]}</Label>
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
                <Label htmlFor="job-prompt">提示词模板</Label>
                <Textarea
                  id="job-prompt"
                  rows={4}
                  value={form.prompt_template}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, prompt_template: event.target.value }))
                  }
                  placeholder="任务触发时发送给 Agent 的指令…"
                />
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={form.enabled ?? true}
                  onCheckedChange={(checked) => setForm((prev) => ({ ...prev, enabled: checked }))}
                />
                <Label>启用任务</Label>
              </div>
              <div className="flex gap-2">
                <Button onClick={() => void handleCreate()} disabled={creating}>
                  {creating ? "创建中…" : "创建"}
                </Button>
                <Button variant="ghost" onClick={() => setShowForm(false)}>
                  取消
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {loading ? (
          <div className="text-muted-foreground flex items-center justify-center gap-2 py-12">
            <Loader2 className="size-4 animate-spin" />
            加载中…
          </div>
        ) : jobs.length === 0 ? (
          <p className="text-muted-foreground py-12 text-center text-sm">暂无自动化任务</p>
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
                        {job.enabled ? "已启用" : "已停用"}
                      </span>
                    </div>
                    <p className="text-muted-foreground line-clamp-2 text-sm">{job.prompt_template}</p>
                    <p className="text-muted-foreground text-xs">
                      {job.trigger_type} · {job.trigger_spec}
                      {job.next_run_at ? ` · 下次 ${formatTime(job.next_run_at)}` : ""}
                    </p>
                    {job.trigger_type === "webhook" && job.webhook_token && (
                      <p className="text-muted-foreground break-all text-xs">
                        Webhook: {webhookUrl(job.webhook_token)}
                      </p>
                    )}
                    {job.last_run_at && (
                      <p className="text-muted-foreground text-xs">
                        上次运行 {formatTime(job.last_run_at)}
                        {job.last_run_status ? ` · ${job.last_run_status}` : ""}
                        {job.last_run_session_id ? (
                          <>
                            {" · "}
                            <a
                              href={`/sessions/${job.last_run_session_id}`}
                              className="text-primary underline"
                            >
                              查看会话
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
                        aria-label="轮换 Webhook 密钥"
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
                      aria-label="删除任务"
                      onClick={() => void handleDelete(job.id)}
                    >
                      <Trash2 className="size-4" />
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
