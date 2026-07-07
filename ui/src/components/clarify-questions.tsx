"use client";

import { useMemo, useState } from "react";
import { HelpCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { ApprovalBar } from "@/components/approval-bar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import type { ClarifyAnswer, ClarifyQuestion } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const CUSTOM_OPTION_ID = "__custom__";

type ClarifyQuestionsProps = {
  title?: string | null;
  questions: ClarifyQuestion[];
  interactive: boolean;
  onSubmit?: (answer: string, structuredAnswers: ClarifyAnswer[]) => Promise<void> | void;
  className?: string;
};

function getPresetSelectionIds(selectedIds: string[]) {
  return selectedIds.filter((id) => id !== CUSTOM_OPTION_ID);
}

function isCustomSelected(selectedIds: string[]) {
  return selectedIds.includes(CUSTOM_OPTION_ID);
}

function isQuestionAnswered(
  question: ClarifyQuestion,
  selections: Record<string, string[]>,
  customAnswers: Record<string, string>,
) {
  const selected = selections[question.id] ?? [];
  const presetSelected = getPresetSelectionIds(selected);
  const customSelected = isCustomSelected(selected);
  const custom = (customAnswers[question.id] ?? "").trim();
  return presetSelected.length > 0 || (customSelected && custom.length > 0);
}

function applyCustomSelection(question: ClarifyQuestion, current: string[]): string[] {
  if (question.allow_multiple) {
    return current.includes(CUSTOM_OPTION_ID) ? current : [...current, CUSTOM_OPTION_ID];
  }
  return [CUSTOM_OPTION_ID];
}

function getAnswerParts(
  question: ClarifyQuestion,
  selectedIds: string[],
  customAnswers: Record<string, string>,
  customPrefix: string,
): string[] {
  const presetIds = getPresetSelectionIds(selectedIds);
  const selectedLabels = question.options
    .filter((option) => presetIds.includes(option.id))
    .map((option) => option.label);
  const customSelected = isCustomSelected(selectedIds);
  const custom = customSelected ? (customAnswers[question.id] ?? "").trim() : "";
  const parts = [...selectedLabels];
  if (custom) parts.push(`${customPrefix}: ${custom}`);
  return parts;
}

function AnswerPills({ labels, className }: { labels: string[]; className?: string }) {
  if (labels.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {labels.map((label) => (
        <span
          key={label}
          className="border-border/70 bg-muted/50 text-foreground inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs"
        >
          {label}
        </span>
      ))}
    </div>
  );
}

export function ClarifyQuestions({
  title,
  questions,
  interactive,
  onSubmit,
  className,
}: ClarifyQuestionsProps) {
  const t = useTranslations("clarify");
  const [selections, setSelections] = useState<Record<string, string[]>>({});
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
  const [customExpanded, setCustomExpanded] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);

  const canSubmit = useMemo(() => {
    if (!interactive || questions.length === 0) return false;
    return questions.every((question) => isQuestionAnswered(question, selections, customAnswers));
  }, [customAnswers, interactive, questions, selections]);

  const selectOption = (question: ClarifyQuestion, optionId: string) => {
    if (!interactive) return;
    setSelections((prev) => {
      const current = prev[question.id] ?? [];
      if (question.allow_multiple) {
        const next = current.includes(optionId)
          ? current.filter((id) => id !== optionId)
          : [...current, optionId];
        return { ...prev, [question.id]: next };
      }
      return { ...prev, [question.id]: [optionId] };
    });
  };

  const handleCustomChange = (question: ClarifyQuestion, value: string) => {
    setCustomAnswers((prev) => ({ ...prev, [question.id]: value }));
    const trimmed = value.trim();
    setSelections((prev) => {
      const current = prev[question.id] ?? [];
      if (!trimmed) {
        return { ...prev, [question.id]: current.filter((id) => id !== CUSTOM_OPTION_ID) };
      }
      return { ...prev, [question.id]: applyCustomSelection(question, current) };
    });
    if (trimmed) {
      setCustomExpanded((prev) => ({ ...prev, [question.id]: true }));
    }
  };

  const toggleCustom = (question: ClarifyQuestion) => {
    if (!interactive) return;
    setCustomExpanded((prev) => {
      const nextExpanded = !prev[question.id];
      if (nextExpanded) {
        const trimmed = (customAnswers[question.id] ?? "").trim();
        if (trimmed) {
          setSelections((current) => ({
            ...current,
            [question.id]: applyCustomSelection(question, current[question.id] ?? []),
          }));
        }
      } else {
        setSelections((current) => ({
          ...current,
          [question.id]: (current[question.id] ?? []).filter((id) => id !== CUSTOM_OPTION_ID),
        }));
      }
      return { ...prev, [question.id]: nextExpanded };
    });
  };

  const buildAnswer = () => {
    const lines = [t("header")];
    for (const question of questions) {
      const selectedIds = selections[question.id] ?? [];
      const parts = getAnswerParts(question, selectedIds, customAnswers, t("customPrefix"));
      lines.push(`- ${question.prompt}: ${parts.join("；")}`);
    }
    return lines.join("\n");
  };

  const buildStructuredAnswers = (): ClarifyAnswer[] => {
    return questions.map((question) => {
      const selectedIds = selections[question.id] ?? [];
      const presetIds = getPresetSelectionIds(selectedIds);
      const selectedLabels = question.options
        .filter((option) => presetIds.includes(option.id))
        .map((option) => option.label);
      const customSelected = isCustomSelected(selectedIds);
      const custom = customSelected ? (customAnswers[question.id] ?? "").trim() : "";
      return {
        question_id: question.id,
        prompt: question.prompt,
        option_ids: presetIds,
        option_labels: selectedLabels,
        custom_text: custom || undefined,
      };
    });
  };

  const handleSubmit = async () => {
    if (!canSubmit || !onSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit(buildAnswer(), buildStructuredAnswers());
    } finally {
      setSubmitting(false);
    }
  };

  if (questions.length === 0) {
    return null;
  }

  const pillClass = (selected: boolean) =>
    cn(
      "inline-flex items-center rounded-full border px-3 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-60",
      selected
        ? "border-primary/60 bg-primary/10 text-primary"
        : "border-border/70 bg-muted/30 text-foreground hover:bg-muted/60",
    );

  return (
    <ApprovalBar tone="blue" className={cn("gap-4 py-3", className)}>
      <div className="flex items-center gap-2">
        <HelpCircle className="text-primary size-4 shrink-0" />
        <p className="text-foreground text-sm font-medium">{title || t("defaultTitle")}</p>
      </div>

      <div className="space-y-4">
        {questions.map((question) => {
          const selectedIds = selections[question.id] ?? [];
          const customSelected = isCustomSelected(selectedIds);
          const showCustomInput = interactive
            ? (customExpanded[question.id] ?? customSelected)
            : customSelected;
          const answerParts = getAnswerParts(
            question,
            selectedIds,
            customAnswers,
            t("customPrefix"),
          );

          return (
            <div key={question.id} className="space-y-2">
              <div className="text-foreground text-sm font-medium">{question.prompt}</div>

              {interactive ? (
                <>
                  <div className="flex flex-wrap gap-1.5">
                    {question.options.map((option) => {
                      const selected = selectedIds.includes(option.id);
                      return (
                        <button
                          key={option.id}
                          type="button"
                          disabled={submitting}
                          className={pillClass(selected)}
                          onClick={() => selectOption(question, option.id)}
                        >
                          {option.label}
                        </button>
                      );
                    })}
                    {(question.allow_custom ?? true) && (
                      <button
                        type="button"
                        disabled={submitting}
                        className={pillClass(customSelected || showCustomInput)}
                        onClick={() => toggleCustom(question)}
                      >
                        {t("otherOption")}
                      </button>
                    )}
                  </div>
                  {(question.allow_custom ?? true) && showCustomInput && (
                    <Textarea
                      value={customAnswers[question.id] ?? ""}
                      disabled={submitting}
                      placeholder={t("customPlaceholder")}
                      className="min-h-8 resize-none text-sm"
                      onChange={(event) => handleCustomChange(question, event.target.value)}
                      onFocus={() =>
                        setCustomExpanded((prev) => ({ ...prev, [question.id]: true }))
                      }
                    />
                  )}
                </>
              ) : (
                <AnswerPills labels={answerParts} />
              )}
            </div>
          );
        })}
      </div>

      {interactive ? (
        <div className="flex justify-end pt-1">
          <Button type="button" size="sm" disabled={!canSubmit || submitting} onClick={() => void handleSubmit()}>
            {submitting ? t("submitting") : t("continue")}
          </Button>
        </div>
      ) : (
        <p className="text-muted-foreground text-xs">{t("submitted")}</p>
      )}
    </ApprovalBar>
  );
}
