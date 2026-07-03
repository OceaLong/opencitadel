"use client";

import { useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { patch } from "@/lib/api/fetch";
import type { ApprovalEventData, PlanStep } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type PlanApprovalBarProps = {
  sessionId: string;
  approval: ApprovalEventData;
  onSend: (message: string) => Promise<void> | void;
  disabled?: boolean;
  className?: string;
};

type PlanPayload = {
  plan?: { steps?: PlanStep[] };
  risk_tools?: string[];
};

function extractSteps(approval: ApprovalEventData): PlanStep[] {
  const payload = approval.payload as PlanPayload;
  return payload.plan?.steps ?? [];
}

export function PlanApprovalBar({
  sessionId,
  approval,
  onSend,
  disabled = false,
  className,
}: PlanApprovalBarProps) {
  const t = useTranslations("planApproval");
  const tCommon = useTranslations("common");
  const [editing, setEditing] = useState(false);
  const [steps, setSteps] = useState<PlanStep[]>(() => extractSteps(approval));
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setSteps(extractSteps(approval));
  }, [approval.approval_id]);

  const riskTools = (approval.payload as PlanPayload).risk_tools ?? [];

  const handleApprove = async () => {
    setSubmitting(true);
    try {
      await onSend("approve");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleApproveWithEdits = async () => {
    setSubmitting(true);
    try {
      await patch(`/sessions/${sessionId}/pending-plan`, {
        plan: { steps },
      });
      await onSend("approve_with_edits");
      setEditing(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("submitEditsFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    const text = feedback.trim();
    if (!text) {
      toast.error(t("rejectReasonRequired"));
      return;
    }
    setSubmitting(true);
    try {
      await onSend(`reject: ${text}`);
      setRejectOpen(false);
      setFeedback("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const updateStepDescription = (index: number, description: string) => {
    setSteps((prev) =>
      prev.map((step, i) => (i === index ? { ...step, description } : step)),
    );
  };

  return (
    <div
      className={cn(
        "border-amber-500/30 bg-amber-500/5 rounded-xl border px-4 py-3 shadow-[var(--shadow-card)]",
        className,
      )}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-foreground text-sm font-medium">{t("title")}</p>
          <p className="text-muted-foreground text-xs">{t("subtitle")}</p>
        </div>
        {!editing && !rejectOpen && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => void handleApprove()} disabled={disabled || submitting}>
              <Check className="size-3.5" />
              {t("approve")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(true)}
              disabled={disabled || submitting}
            >
              <Pencil className="size-3.5" />
              {t("editSteps")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRejectOpen(true)}
              disabled={disabled || submitting}
            >
              <X className="size-3.5" />
              {t("reject")}
            </Button>
          </div>
        )}
      </div>

      {riskTools.length > 0 && (
        <p className="text-muted-foreground mb-2 text-xs">
          {t("riskTools", {
            tools: new Intl.ListFormat(undefined, { style: "short", type: "unit" }).format(riskTools),
          })}
        </p>
      )}

      {editing ? (
        <div className="space-y-2">
          {steps.map((step, index) => (
            <Textarea
              key={step.id || index}
              value={step.description}
              onChange={(event) => updateStepDescription(index, event.target.value)}
              rows={2}
              className="text-sm"
            />
          ))}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void handleApproveWithEdits()} disabled={submitting}>
              {t("saveAndApprove")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              {tCommon("cancel")}
            </Button>
          </div>
        </div>
      ) : (
        <ol className="text-muted-foreground list-decimal space-y-1 pl-5 text-sm">
          {steps.map((step) => (
            <li key={step.id}>{step.description}</li>
          ))}
        </ol>
      )}

      {rejectOpen && (
        <div className="mt-3 space-y-2">
          <Textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder={t("rejectPlaceholder")}
            rows={3}
          />
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" onClick={() => void handleReject()} disabled={submitting}>
              {t("confirmReject")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejectOpen(false)}>
              {tCommon("cancel")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
