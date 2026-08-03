"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle, CheckCircle2, Download, Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

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
import { Textarea } from "@/components/ui/textarea";

import { usePatrolLabels } from "@/hooks/use-patrol-labels";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolFinding, PatrolRunDetail } from "@/lib/api/types";

const statusVariant = (status: string) =>
  status === "pass" || status === "completed"
    ? "success"
    : status === "warn" || status.includes("finding") || status === "skipped"
      ? "warning"
      : status === "fail" || status === "error" || status === "failed"
        ? "destructive"
        : "secondary";

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
  const [decision, setDecision] = useState<{
    finding: PatrolFinding;
    action: "acknowledge" | "resolve" | "false-positive";
  } | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
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
      <Card>
        <CardContent className="grid gap-4 p-5 sm:grid-cols-[1fr_auto]">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold">{t("run.title")}</h1>
              <StatusBadge variant={statusVariant(run.status)}>
                {labels.status[run.status] ?? run.status}
              </StatusBadge>
            </div>
            <p className="text-muted-foreground mt-1 text-xs">
              Run {run.id} · Pack v{run.pack_version} ·{" "}
              {labels.trigger[run.trigger_type] ?? run.trigger_type}
            </p>
          </div>
          <Button variant="outline" onClick={() => void download()}>
            <Download className="size-4" />
            {t("actions.downloadEvidence")}
          </Button>
          <div className="col-span-full grid grid-cols-2 gap-2 sm:grid-cols-6">
            {(["pass", "warn", "fail", "error", "skipped"] as const).map((key) => (
              <div key={key} className="rounded-lg border p-3">
                <p className="text-muted-foreground text-xs uppercase">{key}</p>
                <p className="text-xl font-semibold">{run.counts[key]}</p>
              </div>
            ))}
            <div className="rounded-lg border p-3">
              <p className="text-muted-foreground text-xs">{t("run.evidenceCompleteness")}</p>
              <p className="text-xl font-semibold">
                {Math.round((run.evidence_completeness ?? 0) * 100)}%
              </p>
            </div>
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
            <p className="text-muted-foreground text-sm">{t("run.noFindings")}</p>
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
                  <h3 className="mt-2 font-medium">{finding.title}</h3>
                  <p className="text-muted-foreground mt-1 text-sm">{finding.summary}</p>
                </div>
                {!readOnly && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setDecision({ finding, action: "acknowledge" })}
                    >
                      {t("actions.acknowledge")}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setDecision({ finding, action: "resolve" })}
                    >
                      {t("actions.resolve")}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setDecision({ finding, action: "false-positive" })}
                    >
                      {t("actions.falsePositive")}
                    </Button>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>
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
                <StatusBadge variant={statusVariant(result.status)}>
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
    </div>
  );
}
