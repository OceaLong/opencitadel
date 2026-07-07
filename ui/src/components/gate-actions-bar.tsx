"use client";

import { useState } from "react";
import { Check, Hand, ShieldAlert, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ApprovalBar } from "@/components/approval-bar";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import type { ApprovalEventData } from "@/lib/api/types";

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

const DEFAULT_TOOL_OPTIONS = ["approve", "approve_same", "reject"] as const;
const DEFAULT_TAKEOVER_OPTIONS = ["takeover", "skip"] as const;

function optionLabel(
  option: string,
  t: ReturnType<typeof useTranslations<"gateActions">>,
): string {
  switch (option) {
    case "approve":
      return t("approve");
    case "approve_same":
      return t("approveSame");
    case "reject":
      return t("reject");
    case "takeover":
      return t("takeoverDone");
    case "skip":
      return t("skip");
    default:
      return option;
  }
}

function optionVariant(option: string): "default" | "outline" | "destructive" {
  if (option === "reject") return "outline";
  if (option === "skip") return "outline";
  if (option === "approve_same") return "outline";
  return "default";
}

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

  const handleOptionClick = async (option: string) => {
    if (option === "reject") {
      setRejectOpen(true);
      return;
    }
    await send(option);
  };

  if (approval.kind === "takeover") {
    const options =
      approval.options.length > 0 ? approval.options : [...DEFAULT_TAKEOVER_OPTIONS];

    return (
      <ApprovalBar tone="blue" className={className}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
              <Hand className="size-4" />
              {t("takeoverTitle")}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">{t("takeoverDescription")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {options.map((option) => (
              <Button
                key={option}
                size="sm"
                variant={optionVariant(option)}
                disabled={disabled || submitting}
                onClick={() => void handleOptionClick(option)}
              >
                {optionLabel(option, t)}
              </Button>
            ))}
          </div>
        </div>
      </ApprovalBar>
    );
  }

  if (approval.kind !== "tool") return null;

  const payload = approval.payload as ToolPayload;
  const toolName = payload.tool_name ?? t("unknownTool");
  const argsPreview = payload.args ? JSON.stringify(payload.args, null, 2) : "";
  const isFirstVisit = Boolean(payload.first_visit_domain);
  const domainNote = isFirstVisit
    ? t("firstVisitDomain", { domain: payload.first_visit_domain! })
    : payload.note;
  const scopeLabel =
    operatorScope === "third_party_saas"
      ? t("scopeThirdParty")
      : operatorScope === "owned"
        ? t("scopeOwned")
        : null;

  const options =
    approval.options.length > 0 ? approval.options : [...DEFAULT_TOOL_OPTIONS];
  const visibleOptions = rejectOpen ? options.filter((o) => o !== "reject") : options;
  const showApproveSameHelp = options.includes("approve_same");

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
    <ApprovalBar className={className}>
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
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {isFirstVisit && (
              <p className="text-muted-foreground text-xs">{t("firstVisitHelp")}</p>
            )}
            {showApproveSameHelp && !rejectOpen && (
              <p className="text-muted-foreground text-xs">{t("approveSameHelp")}</p>
            )}
            {scopeLabel && (
              <StatusBadge
                variant={operatorScope === "third_party_saas" ? "warning" : "secondary"}
              >
                {t("targetSystem", { scope: scopeLabel })}
              </StatusBadge>
            )}
          </div>
        </div>
        {!rejectOpen && (
          <div className="flex flex-wrap gap-2">
            {visibleOptions.map((option) => (
              <Button
                key={option}
                size="sm"
                variant={optionVariant(option)}
                disabled={disabled || submitting}
                onClick={() => void handleOptionClick(option)}
              >
                {option === "approve" && <Check className="size-3.5" />}
                {option === "reject" && <X className="size-3.5" />}
                {optionLabel(option, t)}
              </Button>
            ))}
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
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="destructive" disabled={submitting} onClick={() => void handleReject()}>
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
