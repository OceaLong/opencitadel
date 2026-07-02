"use client";

import { useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
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
      toast.error(error instanceof Error ? error.message : "发送失败");
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
      toast.error(error instanceof Error ? error.message : "提交修改失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    const text = feedback.trim();
    if (!text) {
      toast.error("请填写拒绝原因");
      return;
    }
    setSubmitting(true);
    try {
      await onSend(`reject: ${text}`);
      setRejectOpen(false);
      setFeedback("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发送失败");
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
          <p className="text-foreground text-sm font-medium">计划待审批</p>
          <p className="text-muted-foreground text-xs">确认后将开始执行以下步骤</p>
        </div>
        {!editing && !rejectOpen && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => void handleApprove()} disabled={disabled || submitting}>
              <Check className="size-3.5" />
              批准
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(true)}
              disabled={disabled || submitting}
            >
              <Pencil className="size-3.5" />
              编辑步骤
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRejectOpen(true)}
              disabled={disabled || submitting}
            >
              <X className="size-3.5" />
              拒绝
            </Button>
          </div>
        )}
      </div>

      {riskTools.length > 0 && (
        <p className="text-muted-foreground mb-2 text-xs">
          涉及风险工具：{riskTools.join("、")}
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
              保存并批准
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              取消
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
            placeholder="请说明拒绝原因或修改建议…"
            rows={3}
          />
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" onClick={() => void handleReject()} disabled={submitting}>
              确认拒绝
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejectOpen(false)}>
              取消
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
