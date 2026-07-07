"use client";

import { useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ApprovalBar } from "@/components/approval-bar";
import { PlanStepRow } from "@/components/plan-step-row";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { patch } from "@/lib/api/fetch";
import type { ApprovalEventData, PlanStep } from "@/lib/api/types";

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

const DEFAULT_PLAN_OPTIONS = ["approve", "approve_with_edits", "reject"] as const;

function extractSteps(approval: ApprovalEventData): PlanStep[] {
  const payload = approval.payload as PlanPayload;
  return payload.plan?.steps ?? [];
}

function planOptionLabel(
  option: string,
  t: ReturnType<typeof useTranslations<"planApproval">>,
): string {
  switch (option) {
    case "approve":
      return t("approve");
    case "approve_with_edits":
      return t("editSteps");
    case "reject":
      return t("reject");
    default:
      return option;
  }
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
  const options =
    approval.options.length > 0 ? approval.options : [...DEFAULT_PLAN_OPTIONS];

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

  const handleOptionClick = (option: string) => {
    if (option === "approve") {
      void handleApprove();
      return;
    }
    if (option === "approve_with_edits") {
      setEditing(true);
      return;
    }
    if (option === "reject") {
      setRejectOpen(true);
    }
  };

  const updateStepDescription = (index: number, description: string) => {
    setSteps((prev) =>
      prev.map((step, i) => (i === index ? { ...step, description } : step)),
    );
  };

  const actionOptions = options.filter((o) => o !== "approve_with_edits" || !editing);

  return (
    <ApprovalBar className={className}>
      <div className="mb-2 flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="text-foreground text-sm font-medium">{t("title")}</p>
          <p className="text-muted-foreground text-xs">{t("subtitle")}</p>
        </div>
        {!editing && !rejectOpen && (
          <div className="flex flex-wrap gap-2">
            {actionOptions.map((option) => (
              <Button
                key={option}
                size="sm"
                variant={option === "reject" ? "outline" : option === "approve_with_edits" ? "outline" : "default"}
                onClick={() => handleOptionClick(option)}
                disabled={disabled || submitting}
              >
                {option === "approve" && <Check className="size-3.5" />}
                {option === "approve_with_edits" && <Pencil className="size-3.5" />}
                {option === "reject" && <X className="size-3.5" />}
                {planOptionLabel(option, t)}
              </Button>
            ))}
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
        <div className="mt-2 space-y-2">
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
        <div className="bg-muted/40 mt-2 rounded-xl py-1">
          {steps.map((step, index) => (
            <PlanStepRow
              key={step.id || index}
              description={step.description}
              status="pending"
              index={index + 1}
              variant="timeline"
              isLast={index === steps.length - 1}
            />
          ))}
        </div>
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
    </ApprovalBar>
  );
}
