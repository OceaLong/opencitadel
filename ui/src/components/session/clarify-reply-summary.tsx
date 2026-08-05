"use client";

import { useTranslations } from "next-intl";

import type { ClarifyAnswer } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type ClarifyReplySummaryProps = {
  answers: ClarifyAnswer[];
  className?: string;
};

function getAnswerLabels(answer: ClarifyAnswer, customPrefix: string): string[] {
  const labels = [...(answer.option_labels ?? [])];
  const custom = (answer.custom_text ?? "").trim();
  if (custom) {
    labels.push(`${customPrefix}: ${custom}`);
  }
  return labels;
}

export function ClarifyReplySummary({ answers, className }: ClarifyReplySummaryProps) {
  const t = useTranslations("clarify");

  if (answers.length === 0) return null;

  return (
    <div
      className={cn(
        "border-border/70 bg-card text-foreground w-full space-y-3 rounded-2xl border px-3.5 py-2.5 text-sm leading-relaxed shadow-[var(--shadow-card)]",
        className,
      )}
    >
      <p className="text-muted-foreground text-xs font-medium">{t("replyTitle")}</p>
      <div className="space-y-2.5">
        {answers.map((answer) => {
          const labels = getAnswerLabels(answer, t("customPrefix"));
          const prompt = answer.prompt || answer.question_id;
          return (
            <div key={answer.question_id} className="space-y-1">
              <p className="text-muted-foreground text-xs">{prompt}</p>
              <div className="flex flex-wrap gap-1.5">
                {labels.map((label) => (
                  <span
                    key={`${answer.question_id}-${label}`}
                    className="border-border/70 bg-muted/50 text-foreground inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs"
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
