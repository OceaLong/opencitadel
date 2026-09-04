"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { MessageCircleQuestion, X } from "lucide-react";
import { toast } from "sonner";

import { ApprovalBar } from "@/components/session/approval-bar";
import { Button } from "@/components/ui/button";

export type ClarificationCardProps = {
  question: string;
  choices: string[];
  onChoose: (choice: string) => Promise<void> | void;
  onDecline: () => Promise<void> | void;
  disabled?: boolean;
  className?: string;
};

/** 澄清选项卡片：渲染 ask 事件的问题与推荐选项（独立于审批）。 */
export function ClarificationCard({
  question,
  choices,
  onChoose,
  onDecline,
  disabled = false,
  className,
}: ClarificationCardProps) {
  const t = useTranslations("approvalActions");
  const [submitting, setSubmitting] = useState(false);

  const run = async (action: () => Promise<void> | void) => {
    setSubmitting(true);
    try {
      await action();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ApprovalBar className={className}>
      <div className="space-y-2">
        <div>
          <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
            <MessageCircleQuestion className="size-4" />
            {t("clarificationTitle")}
          </p>
          {question ? <p className="text-muted-foreground mt-1 text-sm">{question}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {choices.map((choice, index) => (
            <Button
              key={`${index}-${choice}`}
              size="sm"
              variant="outline"
              disabled={disabled || submitting}
              onClick={() => void run(() => onChoose(choice))}
            >
              {choice}
            </Button>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="text-muted-foreground"
          disabled={disabled || submitting}
          onClick={() => void run(() => onDecline())}
        >
          <X className="size-3.5" />
          {t("clarificationDecline")}
        </Button>
      </div>
    </ApprovalBar>
  );
}
