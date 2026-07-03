"use client";

import { useState } from "react";
import { Check, Hand, ShieldAlert, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import type { ApprovalEventData } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type GateActionsBarProps = {
  approval: ApprovalEventData;
  onSend: (message: string) => Promise<void> | void;
  disabled?: boolean;
  className?: string;
  operatorScope?: string | null;
};

type ToolPayload = {
  tool_name?: string;
  args?: Record<string, unknown>;
  first_visit_domain?: string;
  note?: string;
};

export function GateActionsBar({
  approval,
  onSend,
  disabled = false,
  className,
  operatorScope,
}: GateActionsBarProps) {
  const t = useTranslations("gateActions");
  const tCommon = useTranslations("common");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const send = async (message: string) => {
    setSubmitting(true);
    try {
      await onSend(message);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  if (approval.kind === "takeover") {
    return (
      <div
        className={cn(
          "border-blue-500/30 bg-blue-500/5 rounded-xl border px-4 py-3 shadow-[var(--shadow-card)]",
          className,
        )}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
              <Hand className="size-4" />
              {t("takeoverTitle")}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">{t("takeoverDescription")}</p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={disabled || submitting} onClick={() => void send("takeover")}>
              {t("takeoverDone")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || submitting}
              onClick={() => void send("skip")}
            >
              {t("skip")}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (approval.kind !== "tool") return null;

  const payload = approval.payload as ToolPayload;
  const toolName = payload.tool_name ?? t("unknownTool");
  const argsPreview = payload.args ? JSON.stringify(payload.args, null, 2) : "";
  const domainNote = payload.first_visit_domain
    ? t("firstVisitDomain", { domain: payload.first_visit_domain })
    : payload.note;
  const scopeLabel =
    operatorScope === "third_party_saas"
      ? t("scopeThirdParty")
      : operatorScope === "owned"
        ? t("scopeOwned")
        : null;

  const handleReject = async () => {
    const text = feedback.trim();
    if (!text) {
      toast.error(t("rejectReasonRequired"));
      return;
    }
    await send(`reject: ${text}`);
    setRejectOpen(false);
    setFeedback("");
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
          <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
            <ShieldAlert className="size-4" />
            {t("toolConfirmTitle")}
          </p>
          <p className="text-muted-foreground text-xs">{toolName}</p>
          {domainNote && (
            <p className="text-amber-700 mt-1 text-xs">{domainNote}</p>
          )}
          {scopeLabel && (
            <p className="text-muted-foreground mt-1 text-xs">{t("targetSystem", { scope: scopeLabel })}</p>
          )}
        </div>
        {!rejectOpen && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={disabled || submitting} onClick={() => void send("approve")}>
              <Check className="size-3.5" />
              {t("approve")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || submitting}
              onClick={() => void send("approve_same")}
            >
              {t("approveSame")}
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
        )}
      </div>

      {argsPreview && (
        <pre className="bg-background/60 text-muted-foreground mb-2 max-h-32 overflow-auto rounded-lg p-2 text-xs">
          {argsPreview}
        </pre>
      )}

      {rejectOpen && (
        <div className="space-y-2">
          <Textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder={t("rejectPlaceholder")}
            rows={2}
          />
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" disabled={submitting} onClick={() => void handleReject()}>
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
