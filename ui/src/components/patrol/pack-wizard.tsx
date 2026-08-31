"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Check, ChevronLeft, ChevronRight, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

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

type PatrolTemplateId = "kubernetes-baseline-v1" | "compose-services-baseline-v1";

export function PackWizard() {
  const router = useRouter();
  const t = useTranslations("patrol");
  const { loading: capabilityLoading, capability } = useCapabilities();
  const runAdmissionAvailable = isCapabilityAvailable(capability("ops_patrol"));
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
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    name: t("wizard.defaultName"),
    templateId: "kubernetes-baseline-v1" as PatrolTemplateId,
    serverId: "",
    targetRef: "opencitadel-local",
    cluster: "opencitadel-demo",
    namespace: "opencitadel",
    environment: "staging",
    timezone: "Asia/Shanghai",
    cron: "0 9 * * *",
    scheduleEnabled: false,
  });
  useEffect(() => {
    void integrationsApi
      .listMCPServers()
      .then((data) => setServers(data.items.filter((item) => item.enabled)));
  }, []);
  const selected = useMemo(
    () => servers.find((item) => item.id === form.serverId),
    [servers, form.serverId],
  );
  const checks =
    form.templateId === "compose-services-baseline-v1" ? composeChecks : kubernetesChecks;
  const canContinue =
    step === 0
      ? Boolean(form.serverId && selected?.enabled)
      : step === 1
        ? Boolean(form.cluster && form.namespace)
        : true;

  const create = async () => {
    setSaving(true);
    try {
      const pack = await patrolsApi.createPack({
        name: form.name,
        mcp_server_id: form.serverId,
        template_id: form.templateId,
        config: {
          target_ref: form.targetRef,
          timezone: form.timezone,
          scope: {
            cluster: form.cluster,
            namespaces: [form.namespace],
            environment: form.environment as "dev" | "staging" | "production",
          },
          schedule: { cron: form.cron, enabled: form.scheduleEnabled },
        },
      });
      const requested = await patrolsApi.validatePack(pack.id);
      const validated =
        requested.status === "validating"
          ? await waitForPackValidation(() => patrolsApi.getPack(pack.id))
          : requested;
      if (validated.validation_summary.ok) {
        await patrolsApi.activatePack(pack.id);
        toast.success(t("toast.activated"));
      } else {
        toast.error(t("toast.validationFailed"));
      }
      router.push(`/patrols/${pack.id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.create"));
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
              </div>
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
                  onChange={(e) => setForm({ ...form, targetRef: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-cluster">{t("labels.cluster")}</Label>
                <Input
                  id="patrol-cluster"
                  value={form.cluster}
                  onChange={(e) => setForm({ ...form, cluster: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-namespace">{t("labels.namespace")}</Label>
                <Input
                  id="patrol-namespace"
                  value={form.namespace}
                  onChange={(e) => setForm({ ...form, namespace: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="patrol-environment">{t("labels.environment")}</Label>
                <Select
                  value={form.environment}
                  onValueChange={(value) => setForm({ ...form, environment: value })}
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
            <div className="grid gap-2 sm:grid-cols-2">
              {checks.map((item) => (
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
              <div className="bg-muted/50 rounded-lg border p-4 text-sm sm:col-span-2">
                <p className="font-medium">{t("wizard.notification")}</p>
                <p className="text-muted-foreground mt-1 text-xs">{t("wizard.notificationHint")}</p>
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
              <Button disabled={saving || !canContinue} onClick={() => void create()}>
                {saving && <Loader2 className="size-4 animate-spin" />}
                {t("wizard.createAndDryRun")}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
