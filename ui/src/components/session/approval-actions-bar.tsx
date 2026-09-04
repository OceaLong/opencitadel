"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Check, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";

import { ApprovalBar } from "@/components/session/approval-bar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import type { ApprovalEventData } from "@/lib/api/types";

export type ApprovalActionsBarProps = {
  approval: ApprovalEventData;
  onSend: (decision: "approve" | `reject: ${string}`, feedback?: string) => Promise<void> | void;
  disabled?: boolean;
  className?: string;
};

export function ApprovalActionsBar({
  approval,
  onSend,
  disabled = false,
  className,
}: ApprovalActionsBarProps) {
  const t = useTranslations("approvalActions");
  const tCommon = useTranslations("common");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const payload = approval.payload;

  const send = async (decision: "approve" | `reject: ${string}`, choiceFeedback?: string) => {
    setSubmitting(true);
    try {
      await onSend(decision, choiceFeedback);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const reject = async () => {
    const reason = feedback.trim();
    if (!reason) {
      toast.error(t("rejectReasonRequired"));
      return;
    }
    await send(`reject: ${reason}`);
    setRejectOpen(false);
    setFeedback("");
  };

  return (
    <ApprovalBar className={className}>
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
            <ShieldAlert className="size-4" />
            {t("toolConfirmTitle")}
          </p>
          <p className="text-muted-foreground text-xs">{payload.tool_name ?? t("unknownTool")}</p>
          {payload.note ? <p className="text-warning mt-1 text-xs">{payload.note}</p> : null}
        </div>
        {!rejectOpen ? (
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={disabled || submitting}
              onClick={() => void send("approve")}
            >
              <Check className="size-3.5" />
              {t("approve")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || submitting}
              onClick={() => setRejectOpen(true)}
            >
              <X className="size-3.5" />
              {t("reject")}
            </Button>
          </div>
        ) : null}
      </div>

      {rejectOpen ? (
        <div className="space-y-2">
          <Textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder={t("rejectPlaceholder")}
            rows={2}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="destructive"
              disabled={submitting}
              onClick={() => void reject()}
            >
              {t("confirmReject")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejectOpen(false)}>
              {tCommon("cancel")}
            </Button>
          </div>
        </div>
      ) : null}
    </ApprovalBar>
  );
}
