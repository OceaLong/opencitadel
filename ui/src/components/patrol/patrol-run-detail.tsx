"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  Link2,
  Loader2,
  MoreHorizontal,
  ShieldAlert,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { RemediationDialog } from "@/components/patrol/remediation-dialog";
import { RemediationStatusList } from "@/components/patrol/remediation-status";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";

import { useCapabilities } from "@/hooks/use-capabilities";
import { patrolStatusVariant, usePatrolLabels } from "@/hooks/use-patrol-labels";
import { canProposePatrolRemediation } from "@/lib/api/capabilities";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolFinding, PatrolRemediation, PatrolRunDetail } from "@/lib/api/types";

export function PatrolRunDetailView({
  run,
  readOnly,
  onRefresh,
}: {
  run: PatrolRunDetail;
  readOnly: boolean;
  onRefresh: () => void;
}) {
  const t = useTranslations("patrol");
  const labels = usePatrolLabels();
  const { loading: capabilityLoading, capability } = useCapabilities();
  const remediationCapability = capability("ops_patrol_remediation");
  const remediationProposalAvailable = canProposePatrolRemediation(remediationCapability);
  const remediationExecutionAvailable = remediationCapability?.state === "available";
  const [decision, setDecision] = useState<{
    finding: PatrolFinding;
    action: "acknowledge" | "resolve" | "false-positive";
  } | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [remediations, setRemediations] = useState<PatrolRemediation[]>([]);
  const [remediationTarget, setRemediationTarget] = useState<PatrolFinding | null>(null);
  const loadRemediations = async () => {
    try {
      const list = await patrolsApi.listRemediations(run.id);
      setRemediations(list.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("remediation.errors.listLoad"));
    }
  };
  useEffect(() => {
    void loadRemediations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.id]);
  const download = async () => {
    try {
      const blob = await patrolsApi.downloadEvidence(run.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `patrol-${run.id}.zip`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.evidenceDownload"));
    }
  };
  const submit = async () => {
    if (!decision || (decision.action === "false-positive" && !reason.trim())) return;
    setSaving(true);
    try {
      await patrolsApi.decideFinding(decision.finding.id, decision.action, reason);
      setDecision(null);
      setReason("");
      onRefresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.findingUpdate"));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="grid gap-5">
      <Card className="bg-background/95 sticky top-0 z-10 backdrop-blur">
        <CardContent className="grid gap-4 p-5">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold">{t("run.title")}</h1>
              <StatusBadge variant={patrolStatusVariant(run.status)}>
                {labels.status[run.status] ?? run.status}
              </StatusBadge>
            </div>
            <p className="text-muted-foreground mt-1 text-xs">
              {t("labels.run")} {run.id} · {t("labels.pack")}{" "}
              <span translate="no">v{run.pack_version}</span> ·{" "}
              {labels.trigger[run.trigger_type] ?? run.trigger_type}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {(["pass", "warn", "fail", "error", "skipped"] as const).map((key) => (
              <div key={key} className="rounded-lg border p-3">
                <p className="text-muted-foreground text-xs uppercase">{key}</p>
                <p className="font-mono text-xl font-semibold">{run.counts[key]}</p>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
            <Link2 className="text-muted-foreground size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-muted-foreground text-xs">{t("run.evidenceCompleteness")}</p>
              <p className="font-mono text-sm font-semibold">
                {Math.round((run.evidence_completeness ?? 0) * 100)}%
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void download()}>
              <Download className="size-4" />
              {t("actions.downloadEvidence")}
            </Button>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldAlert className="size-5" />
            {t("run.needsAttention")}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          {run.findings.length === 0 ? (
            <EmptyState title={t("run.noFindings")} />
          ) : (
            run.findings.map((finding) => (
              <div
                key={finding.id}
                className="grid gap-3 rounded-lg border p-4 sm:grid-cols-[1fr_auto]"
              >
                <div>
                  <div className="flex gap-2">
                    <StatusBadge
                      variant={finding.severity === "critical" ? "destructive" : "warning"}
                    >
                      {labels.severity[finding.severity] ?? finding.severity}
                    </StatusBadge>
                    <StatusBadge>
                      {labels.findingStatus[finding.status] ?? finding.status}
                    </StatusBadge>
                  </div>
                  <h3 className="mt-2 text-sm font-medium">{finding.title}</h3>
                  <p className="text-muted-foreground text-dense mt-1">{finding.summary}</p>
                </div>
                {!readOnly && (
                  <div className="flex items-start gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-warning/40 text-warning hover:bg-approval-subtle"
                      disabled={capabilityLoading || !remediationProposalAvailable}
                      title={
                        remediationProposalAvailable ? undefined : t("remediation.unavailable")
                      }
                      onClick={() => setRemediationTarget(finding)}
                    >
                      {t("actions.remediate")}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button size="icon-sm" variant="ghost">
                          <MoreHorizontal className="size-4" />
                          <span className="sr-only">{t("decision.title")}</span>
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onSelect={() => setDecision({ finding, action: "acknowledge" })}
                        >
                          {t("actions.acknowledge")}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => setDecision({ finding, action: "resolve" })}
                        >
                          {t("actions.resolve")}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => setDecision({ finding, action: "false-positive" })}
                        >
                          {t("actions.falsePositive")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>
      <RemediationStatusList remediations={remediations} />
      <Card>
        <CardHeader>
          <CardTitle>{t("run.allChecks")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          {run.check_results.map((result) => (
            <details key={result.id} className="group rounded-lg border p-4">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                <span className="flex items-center gap-2">
                  {result.status === "pass" ? (
                    <CheckCircle2 className="text-success size-4" />
                  ) : (
                    <AlertCircle className="text-destructive size-4" />
                  )}
                  <span className="font-medium">{result.check_id}</span>
                </span>
                <StatusBadge variant={patrolStatusVariant(result.status)}>
                  {labels.status[result.status] ?? result.status}
                </StatusBadge>
              </summary>
              <div className="mt-4 grid gap-3 text-sm">
                <p>{result.explanation || result.error_message || "—"}</p>
                <div>
                  <p className="font-medium">{t("run.observed")}</p>
                  <pre className="bg-muted mt-1 max-h-64 overflow-auto rounded p-3 text-xs">
                    {JSON.stringify(result.observed, null, 2)}
                  </pre>
                </div>
                <div>
                  <p className="font-medium">{t("run.assertions")}</p>
                  <pre className="bg-muted mt-1 max-h-64 overflow-auto rounded p-3 text-xs">
                    {JSON.stringify(result.assertion_results, null, 2)}
                  </pre>
                </div>
                <div>
                  <p className="font-medium">{t("run.evidenceRefs")}</p>
                  <pre className="bg-muted mt-1 max-h-64 overflow-auto rounded p-3 text-xs">
                    {JSON.stringify(result.evidence_refs, null, 2)}
                  </pre>
                </div>
              </div>
            </details>
          ))}
        </CardContent>
      </Card>
      <Dialog open={decision !== null} onOpenChange={(open) => !open && setDecision(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("decision.title")}</DialogTitle>
          </DialogHeader>
          <Textarea
            autoFocus
            placeholder={
              decision?.action === "false-positive"
                ? t("decision.falsePositiveReason")
                : t("decision.notes")
            }
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setDecision(null)}>
              {t("actions.cancel")}
            </Button>
            <Button
              disabled={saving || (decision?.action === "false-positive" && !reason.trim())}
              onClick={() => void submit()}
            >
              {saving && <Loader2 className="size-4 animate-spin" />}
              {t("actions.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {remediationTarget && (
        <RemediationDialog
          open={remediationTarget !== null}
          onOpenChange={(open) => {
            if (!open) {
              setRemediationTarget(null);
              void loadRemediations();
            }
          }}
          finding={remediationTarget}
          run={run}
          executionAvailable={remediationExecutionAvailable}
        />
      )}
    </div>
  );
}
