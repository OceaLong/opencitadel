"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
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

import { usePatrolLabels } from "@/hooks/use-patrol-labels";
import { patrolsApi } from "@/lib/api/patrols";
import type {
  PatrolFinding,
  PatrolPackConfig,
  PatrolRemediationAction,
  PatrolRunDetail,
} from "@/lib/api/types";

type ProbeCheck = PatrolPackConfig["checks"][number];

export function RemediationDialog({
  open,
  onOpenChange,
  finding,
  run,
  executionAvailable,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  finding: PatrolFinding;
  run: PatrolRunDetail;
  executionAvailable: boolean;
}) {
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  const router = useRouter();
  const [check, setCheck] = useState<ProbeCheck | null>(null);
  const [loadingCheck, setLoadingCheck] = useState(false);
  const [action, setAction] = useState<PatrolRemediationAction | "">("");
  const [replicas, setReplicas] = useState("1");
  const [workloadOverride, setWorkloadOverride] = useState("");
  const [impactConfirmed, setImpactConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCheck(null);
    setAction("");
    setReplicas("1");
    setWorkloadOverride("");
    setImpactConfirmed(false);
    setLoadingCheck(true);
    let active = true;
    void (async () => {
      try {
        const pack = await patrolsApi.getPack(run.pack_id);
        if (!active) return;
        const checkResult = run.check_results.find((item) => item.id === finding.check_result_id);
        const found = pack.config.checks.find((item) => item.id === checkResult?.check_id) ?? null;
        setCheck(found);
      } catch (error) {
        if (active)
          toast.error(error instanceof Error ? error.message : t("remediation.errors.checkLoad"));
      } finally {
        if (active) setLoadingCheck(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [open, finding.check_result_id, run.pack_id, run.check_results, t]);

  const allowedActions = finding.allowed_actions;
  const namespace = String(check?.probe.args.namespace ?? "");
  const kind = String(check?.probe.args.kind ?? "Deployment");
  const detectedWorkload = String(check?.probe.args.workload ?? "");
  const workloadRequired = !detectedWorkload;
  const effectiveWorkload = workloadOverride.trim() || detectedWorkload;

  const replicasValue = Number(replicas);
  const replicasValid = Number.isInteger(replicasValue) && replicasValue > 0;

  const canSubmit =
    !loadingCheck &&
    action !== "" &&
    (!workloadRequired || workloadOverride.trim() !== "") &&
    (action !== "scale_workload" || replicasValid) &&
    impactConfirmed;

  const submit = async () => {
    // `canSubmit` includes `action !== ""` as one of its conjuncts, so TS
    // narrows `action` to `PatrolRemediationAction` for the rest of this
    // closure once this guard passes (control-flow analysis of aliased
    // conditions) — no separate `action === ""` check is reachable here.
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const params: Record<string, unknown> =
        action === "scale_workload" ? { replicas: replicasValue } : {};
      const remediation = await patrolsApi.proposeRemediation(finding.id, {
        action,
        params,
        workload: workloadOverride.trim() || undefined,
      });
      onOpenChange(false);
      if (remediation.session_id) {
        router.push(`/sessions/${remediation.session_id}`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("remediation.errors.propose"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("remediation.dialog.title")}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          {!executionAvailable && (
            <p className="border-warning/30 bg-warning/5 rounded-lg border p-3 text-sm">
              {t("remediation.proposeOnlyNotice")}
            </p>
          )}
          <div className="grid gap-2">
            <Label htmlFor="remediation-action">{t("remediation.dialog.actionLabel")}</Label>
            <Select
              value={action}
              onValueChange={(value) => setAction(value as PatrolRemediationAction)}
              disabled={loadingCheck || allowedActions.length === 0}
            >
              <SelectTrigger id="remediation-action">
                <SelectValue placeholder={t("remediation.dialog.actionPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {allowedActions.map((item) => (
                  <SelectItem key={item} value={item}>
                    {labels.remediationAction[item] ?? item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!loadingCheck && allowedActions.length === 0 && (
              <p className="text-destructive text-xs">
                {t("remediation.dialog.noActionsAvailable")}
              </p>
            )}
          </div>
          {!loadingCheck && check && (
            <p className="text-muted-foreground text-xs">
              {t("remediation.dialog.targetPreview", {
                kind,
                workload: effectiveWorkload || "?",
                namespace,
              })}
            </p>
          )}
          <div className="grid gap-2">
            <Label htmlFor="remediation-workload">{t("remediation.dialog.workloadLabel")}</Label>
            <Input
              id="remediation-workload"
              value={workloadOverride}
              placeholder={detectedWorkload || undefined}
              onChange={(event) => setWorkloadOverride(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              {workloadRequired
                ? t("remediation.dialog.workloadRequiredHint")
                : t("remediation.dialog.workloadDetectedHint", { workload: detectedWorkload })}
            </p>
          </div>
          {action === "scale_workload" && (
            <div className="grid gap-2">
              <Label htmlFor="remediation-replicas">{t("remediation.dialog.replicasLabel")}</Label>
              <Input
                id="remediation-replicas"
                type="number"
                min={1}
                value={replicas}
                onChange={(event) => setReplicas(event.target.value)}
              />
            </div>
          )}
          {action === "rollback_workload" && (
            <p className="text-muted-foreground text-xs">
              {t("remediation.dialog.rollbackToPreviousHint")}
            </p>
          )}
          <div className="flex items-start gap-2 rounded-lg border p-3 text-sm">
            <Checkbox
              id="remediation-impact-confirm"
              checked={impactConfirmed}
              onCheckedChange={(checked) => setImpactConfirmed(checked === true)}
            />
            <Label htmlFor="remediation-impact-confirm" className="font-normal">
              {t("remediation.dialog.impactConfirm")}
            </Label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("actions.cancel")}
          </Button>
          <Button disabled={!canSubmit || submitting} onClick={() => void submit()}>
            {submitting && <Loader2 className="size-4 animate-spin" />}
            {t("remediation.dialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
