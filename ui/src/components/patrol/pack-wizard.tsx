"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Check, ChevronLeft, ChevronRight, Info, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { NotifyChannelsField } from "@/components/patrol/notify-channels-field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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

import { useCapabilities } from "@/hooks/use-capabilities";
import { integrationsApi, type MCPServer } from "@/lib/api";
import { isCapabilityAvailable } from "@/lib/api/capabilities";
import { waitForPackValidation } from "@/lib/api/patrol-validation";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolNotifyChannel, PatrolPack, PatrolPackConfig } from "@/lib/api/types";

type PatrolTemplateId = "kubernetes-baseline-v1" | "compose-services-baseline-v1";

/** 通知渠道按类型的关键字段：为空的渠道行在提交时丢弃。 */
function isNotifyChannelComplete(channel: PatrolNotifyChannel): boolean {
  if (channel.type === "mcp") return Boolean(channel.server_id);
  if (channel.type === "webhook") return Boolean(channel.url);
  return Boolean(channel.address);
}

/**
 * 巡检 Pack 创建/编辑向导。传入 `pack` 时进入编辑模式：表单预填现有配置，
 * 提交走 PATCH updatePack（乐观锁 version），保存后同样走验证 → 激活流程。
 */
export function PackWizard({ pack }: { pack?: PatrolPack }) {
  const router = useRouter();
  const t = useTranslations("patrol");
  const { loading: capabilityLoading, capability } = useCapabilities();
  const runAdmissionAvailable = isCapabilityAvailable(capability("ops_patrol"));
  const editing = Boolean(pack);
  const steps = [
    t("wizard.steps.target"),
    t("wizard.steps.scope"),
    t("wizard.steps.checks"),
    t("wizard.steps.schedule"),
  ];
  const kubernetesChecks = [
    {
      name: t("checks.availability"),
      tool: "k8s_workload_summary",
      threshold: t("thresholds.availability"),
    },
    {
      name: t("checks.restarts"),
      tool: "k8s_workload_summary",
      threshold: t("thresholds.restarts"),
    },
    { name: t("checks.pending"), tool: "k8s_workload_summary", threshold: t("thresholds.pending") },
    { name: t("checks.events"), tool: "k8s_recent_events", threshold: t("thresholds.events") },
    {
      name: t("checks.pressure"),
      tool: "k8s_workload_summary + prom_query",
      threshold: t("thresholds.pressure"),
    },
    { name: t("checks.http5xx"), tool: "prom_query", threshold: t("thresholds.http5xx") },
    { name: t("checks.tls"), tool: "certificate_status", threshold: t("thresholds.tls") },
    { name: t("checks.backup"), tool: "backup_status", threshold: t("thresholds.backup") },
    {
      name: t("checks.dependency"),
      tool: "dependency_status",
      threshold: t("thresholds.dependency"),
    },
    { name: t("checks.endpoint"), tool: "http_probe", threshold: t("thresholds.endpoint") },
  ];
  const composeChecks = [
    { name: t("checks.apiHealth"), tool: "http_probe", threshold: t("thresholds.healthy") },
    { name: t("checks.apiStatus"), tool: "http_probe", threshold: t("thresholds.status200") },
    { name: t("checks.apiLatency"), tool: "http_probe", threshold: t("thresholds.latency") },
    {
      name: t("checks.apiIntegrity"),
      tool: "http_probe",
      threshold: t("thresholds.responseIntegrity"),
    },
    {
      name: t("checks.consoleHealth"),
      tool: "http_probe",
      threshold: t("thresholds.healthy"),
    },
    {
      name: t("checks.consoleStatus"),
      tool: "http_probe",
      threshold: t("thresholds.status200"),
    },
    {
      name: t("checks.consoleLatency"),
      tool: "http_probe",
      threshold: t("thresholds.latency"),
    },
    {
      name: t("checks.consoleIntegrity"),
      tool: "http_probe",
      threshold: t("thresholds.responseIntegrity"),
    },
    {
      name: t("checks.primaryDependencies"),
      tool: "dependency_status",
      threshold: t("thresholds.dependency"),
    },
    {
      name: t("checks.consoleConnectivity"),
      tool: "dependency_status",
      threshold: t("thresholds.dependency"),
    },
  ];
  const [step, setStep] = useState(0);
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [serversLoaded, setServersLoaded] = useState(false);
  const [serversError, setServersError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(() => ({
    name: pack?.name ?? t("wizard.defaultName"),
    templateId: "kubernetes-baseline-v1" as PatrolTemplateId,
    serverId: pack?.mcp_server_id ?? "",
    targetRef: pack?.config.target_ref ?? "",
    cluster: pack?.config.scope.cluster ?? "",
    namespaces: pack?.config.scope.namespaces.join(", ") ?? "",
    environment: pack?.config.scope.environment ?? "staging",
    timezone: pack?.config.timezone ?? "Asia/Shanghai",
    cron: pack?.config.schedule.cron ?? "0 9 * * *",
    scheduleEnabled: pack?.config.schedule.enabled ?? false,
    notifyChannels: (pack?.config.notify_channels ?? []) as PatrolNotifyChannel[],
  }));
  useEffect(() => {
    let active = true;
    void integrationsApi
      .listMCPServers()
      .then((data) => {
        if (!active) return;
        setServers(data.items.filter((item) => item.enabled));
        setServersError(false);
      })
      .catch(() => {
        // 采集器列表拉取失败不能让向导变成"下拉空 + 下一步禁用"的死路：
        // 标记错误态，在第 1 步给出明确指引。
        if (active) setServersError(true);
      })
      .finally(() => {
        if (active) setServersLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);
  const selected = useMemo(
    () => servers.find((item) => item.id === form.serverId),
    [servers, form.serverId],
  );
  const parsedNamespaces = useMemo(
    () =>
      form.namespaces
        .split(/[,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    [form.namespaces],
  );
  const templateChecks =
    form.templateId === "compose-services-baseline-v1" ? composeChecks : kubernetesChecks;
  const canContinue =
    step === 0
      ? Boolean(form.serverId && (editing || selected?.enabled))
      : step === 1
        ? Boolean(form.targetRef && form.cluster && parsedNamespaces.length > 0)
        : true;

  const buildConfig = (): Partial<PatrolPackConfig> => ({
    target_ref: form.targetRef,
    timezone: form.timezone,
    scope: {
      cluster: form.cluster,
      namespaces: parsedNamespaces,
      environment: form.environment as "dev" | "staging" | "production",
    },
    schedule: { cron: form.cron, enabled: form.scheduleEnabled },
    notify_channels: form.notifyChannels.filter(isNotifyChannelComplete),
  });

  const validateAndActivate = async (packId: string) => {
    const requested = await patrolsApi.validatePack(packId);
    const validated =
      requested.status === "validating"
        ? await waitForPackValidation(() => patrolsApi.getPack(packId))
        : requested;
    if (validated.validation_summary.ok) {
      await patrolsApi.activatePack(packId);
      toast.success(t("toast.activated"));
    } else {
      toast.error(t("toast.validationFailed"));
    }
  };

  const create = async () => {
    setSaving(true);
    try {
      const created = await patrolsApi.createPack({
        name: form.name,
        mcp_server_id: form.serverId,
        template_id: form.templateId,
        config: buildConfig(),
      });
      await validateAndActivate(created.id);
      router.push(`/patrols/${created.id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.create"));
    } finally {
      setSaving(false);
    }
  };

  const update = async () => {
    if (!pack) return;
    setSaving(true);
    try {
      const updated = await patrolsApi.updatePack(pack.id, {
        version: pack.version,
        name: form.name,
        mcp_server_id: form.serverId,
        config: { ...pack.config, ...buildConfig() },
      });
      await validateAndActivate(updated.id);
      router.push(`/patrols/${pack.id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.update"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid gap-5">
      {!capabilityLoading && !runAdmissionAvailable && (
        <div
          role="status"
          className="border-warning/40 bg-approval-subtle text-warning rounded-lg border p-3 text-sm"
        >
          {t("disabled.description")}
        </div>
      )}
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label={t("wizard.stepsAria")}>
        {steps.map((label, index) => (
          <li
            key={label}
            className={`rounded-lg border px-3 py-2 text-xs ${index === step ? "border-primary bg-primary/5 font-medium" : "text-muted-foreground"}`}
          >
            {index + 1}. {label}
          </li>
        ))}
      </ol>
      <Card>
        <CardHeader>
          <CardTitle>{steps[step]}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-5">
          {step === 0 && (
            <>
              <div className="grid gap-2">
                <Label htmlFor="patrol-name">{t("wizard.name")}</Label>
                <Input
                  id="patrol-name"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-collector">{t("wizard.collector")}</Label>
                <Select
                  value={form.serverId}
                  onValueChange={(value) => setForm({ ...form, serverId: value })}
                >
                  <SelectTrigger id="patrol-collector">
                    <SelectValue placeholder={t("wizard.collectorPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {servers.map((server) => (
                      <SelectItem key={server.id} value={server.id}>
                        {server.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {serversLoaded && serversError && (
                  <p className="text-destructive text-xs" role="status">
                    {t("wizard.collectorLoadFailed")}
                  </p>
                )}
                {serversLoaded && !serversError && servers.length === 0 && (
                  <p className="text-muted-foreground text-xs" role="status">
                    {t("wizard.collectorEmpty")}
                  </p>
                )}
              </div>
              {!editing && (
                <div className="grid gap-2">
                  <Label htmlFor="patrol-template">{t("wizard.template")}</Label>
                  <Select
                    value={form.templateId}
                    onValueChange={(value) =>
                      setForm({ ...form, templateId: value as PatrolTemplateId })
                    }
                  >
                    <SelectTrigger id="patrol-template">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="kubernetes-baseline-v1">
                        {t("wizard.templates.kubernetes")}
                      </SelectItem>
                      <SelectItem value="compose-services-baseline-v1">
                        {t("wizard.templates.composeServices")}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div className="border-success/30 bg-success/5 flex gap-3 rounded-lg border p-3 text-sm">
                <ShieldCheck className="text-success size-5 shrink-0" />
                <span>{t("wizard.readOnlyBoundary")}</span>
              </div>
            </>
          )}
          {step === 1 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="patrol-target-ref">{t("labels.targetRef")}</Label>
                <Input
                  id="patrol-target-ref"
                  value={form.targetRef}
                  translate="no"
                  placeholder={t("wizard.targetRefPlaceholder")}
                  onChange={(e) => setForm({ ...form, targetRef: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-cluster">{t("labels.cluster")}</Label>
                <Input
                  id="patrol-cluster"
                  value={form.cluster}
                  translate="no"
                  placeholder={t("wizard.clusterPlaceholder")}
                  onChange={(e) => setForm({ ...form, cluster: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-namespaces">{t("labels.namespace")}</Label>
                <Input
                  id="patrol-namespaces"
                  value={form.namespaces}
                  translate="no"
                  placeholder={t("wizard.namespacesPlaceholder")}
                  onChange={(e) => setForm({ ...form, namespaces: e.target.value })}
                />
                <p className="text-muted-foreground text-xs">{t("wizard.namespacesHint")}</p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-environment">{t("labels.environment")}</Label>
                <Select
                  value={form.environment}
                  onValueChange={(value) =>
                    setForm({
                      ...form,
                      environment: value as "dev" | "staging" | "production",
                    })
                  }
                >
                  <SelectTrigger id="patrol-environment">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["dev", "staging", "production"].map((item) => (
                      <SelectItem value={item} key={item}>
                        {item}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
          {step === 2 && (
            <>
              <div className="bg-muted/50 text-muted-foreground flex gap-2 rounded-lg border p-3 text-xs">
                <Info className="mt-0.5 size-4 shrink-0" />
                <span>{t("wizard.checksPreviewNotice")}</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {editing && pack
                  ? pack.config.checks.map((item) => (
                      <div
                        className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 rounded-lg border p-3 text-sm"
                        key={item.id}
                      >
                        <Check className="text-success mt-0.5 size-4" />
                        <span className="font-medium">{item.title}</span>
                        <span className="text-muted-foreground col-start-2 text-xs break-all">
                          {t("wizard.source")}: <code>{item.probe.tool}</code>
                        </span>
                        <span className="text-muted-foreground col-start-2 text-xs">
                          {t("wizard.permission")}: {t("wizard.readPermission")}
                        </span>
                      </div>
                    ))
                  : templateChecks.map((item) => (
                      <div
                        className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 rounded-lg border p-3 text-sm"
                        key={item.name}
                      >
                        <Check className="text-success mt-0.5 size-4" />
                        <span className="font-medium">{item.name}</span>
                        <span className="text-muted-foreground col-start-2 text-xs">
                          {t("wizard.threshold")}: {item.threshold}
                        </span>
                        <span className="text-muted-foreground col-start-2 text-xs break-all">
                          {t("wizard.source")}: <code>{item.tool}</code>
                        </span>
                        <span className="text-muted-foreground col-start-2 text-xs">
                          {t("wizard.permission")}: {t("wizard.readPermission")}
                        </span>
                      </div>
                    ))}
              </div>
            </>
          )}
          {step === 3 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="patrol-timezone">{t("wizard.timezone")}</Label>
                <Input
                  id="patrol-timezone"
                  value={form.timezone}
                  onChange={(e) => setForm({ ...form, timezone: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-cron">{t("wizard.dailyCron")}</Label>
                <Input
                  id="patrol-cron"
                  value={form.cron}
                  onChange={(e) => setForm({ ...form, cron: e.target.value })}
                />
                <p className="text-muted-foreground text-xs">{t("wizard.cronHint")}</p>
              </div>
              <div className="flex items-center justify-between gap-4 rounded-lg border p-4 sm:col-span-2">
                <div>
                  <Label htmlFor="patrol-schedule-enabled">{t("wizard.scheduleEnabled")}</Label>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {t("wizard.scheduleEnabledHint")}
                  </p>
                </div>
                <Switch
                  id="patrol-schedule-enabled"
                  checked={form.scheduleEnabled}
                  onCheckedChange={(checked) => setForm({ ...form, scheduleEnabled: checked })}
                />
              </div>
              <div className="bg-muted/50 grid gap-3 rounded-lg border p-4 text-sm sm:col-span-2">
                <div>
                  <p className="font-medium">{t("wizard.notification")}</p>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {t("wizard.notificationHint")}
                  </p>
                </div>
                <NotifyChannelsField
                  value={form.notifyChannels}
                  onChange={(notifyChannels) => setForm({ ...form, notifyChannels })}
                  servers={servers}
                />
              </div>
            </div>
          )}
          <div className="flex justify-between border-t pt-4">
            <Button
              variant="outline"
              disabled={step === 0 || saving}
              onClick={() => setStep((value) => value - 1)}
            >
              <ChevronLeft className="size-4" />
              {t("wizard.previous")}
            </Button>
            {step < steps.length - 1 ? (
              <Button disabled={!canContinue} onClick={() => setStep((value) => value + 1)}>
                {t("wizard.next")}
                <ChevronRight className="size-4" />
              </Button>
            ) : (
              <Button
                disabled={saving || !canContinue}
                onClick={() => void (editing ? update() : create())}
              >
                {saving && <Loader2 className="size-4 animate-spin" />}
                {editing ? t("wizard.updateAndRevalidate") : t("wizard.createAndDryRun")}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
