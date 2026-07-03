"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, ChevronLeft, ChevronRight, Circle, HelpCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
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

function applyCustomSelection(
  question: ClarifyQuestion,
  current: string[],
): string[] {
  if (question.allow_multiple) {
    return current.includes(CUSTOM_OPTION_ID)
      ? current
      : [...current, CUSTOM_OPTION_ID];
  }
  return [CUSTOM_OPTION_ID];
}

export function ClarifyQuestions({
  title,
  questions,
  interactive,
  onSubmit,
  className,
}: ClarifyQuestionsProps) {
  const t = useTranslations("clarify");
  const [step, setStep] = useState(0);
  const [selections, setSelections] = useState<Record<string, string[]>>({});
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const currentQuestion = questions[step];
  const progress = questions.length > 0 ? ((step + 1) / questions.length) * 100 : 0;

  const canAdvance = useMemo(() => {
    if (!interactive || !currentQuestion) return false;
    return isQuestionAnswered(currentQuestion, selections, customAnswers);
  }, [currentQuestion, customAnswers, interactive, selections]);

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
  };

  const selectCustom = (question: ClarifyQuestion) => {
    if (!interactive) return;
    const trimmed = (customAnswers[question.id] ?? "").trim();
    if (!trimmed) return;
    setSelections((prev) => {
      const current = prev[question.id] ?? [];
      return { ...prev, [question.id]: applyCustomSelection(question, current) };
    });
  };

  const deselectCustom = (question: ClarifyQuestion) => {
    if (!interactive) return;
    setSelections((prev) => ({
      ...prev,
      [question.id]: (prev[question.id] ?? []).filter((id) => id !== CUSTOM_OPTION_ID),
    }));
  };

  const buildAnswer = () => {
    const lines = [t("header")];
    for (const question of questions) {
      const selectedIds = selections[question.id] ?? [];
      const presetIds = getPresetSelectionIds(selectedIds);
      const selectedLabels = question.options
        .filter((option) => presetIds.includes(option.id))
        .map((option) => option.label);
      const customSelected = isCustomSelected(selectedIds);
      const custom = customSelected ? (customAnswers[question.id] ?? "").trim() : "";
      const parts = [...selectedLabels];
      if (custom) parts.push(`${t("customPrefix")}: ${custom}`);
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

  const handleNext = () => {
    if (!canAdvance) return;
    if (step < questions.length - 1) {
      setStep((prev) => prev + 1);
    } else {
      void handleSubmit();
    }
  };

  if (questions.length === 0) {
    return null;
  }

  const currentSelected = currentQuestion ? (selections[currentQuestion.id] ?? []) : [];
  const currentCustomSelected = isCustomSelected(currentSelected);

  return (
    <Card className={cn("gap-4 py-4", className)}>
      <CardHeader className="gap-2 px-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <HelpCircle className="text-primary size-4" />
          <span>{title || t("defaultTitle")}</span>
        </CardTitle>
        {interactive && questions.length > 1 && (
          <div className="space-y-2">
            <div className="text-muted-foreground flex items-center justify-between text-xs">
              <span>
                {t("questionProgress", { current: step + 1, total: questions.length })}
              </span>
            </div>
            <Progress value={progress} className="h-1.5" />
          </div>
        )}
      </CardHeader>
      <CardContent className="min-h-[220px] space-y-4 px-4">
        {currentQuestion && (
          <div key={currentQuestion.id} className="animate-in fade-in-0 space-y-2 duration-200">
            <div className="text-foreground text-sm font-medium">{currentQuestion.prompt}</div>
            <div className="flex flex-col gap-2">
              {currentQuestion.options.map((option) => {
                const selected = currentSelected.includes(option.id);
                return (
                  <button
                    key={option.id}
                    type="button"
                    disabled={!interactive || submitting}
                    className={cn(
                      "border-border/70 hover:bg-muted/60 flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                      selected && "border-primary/60 bg-primary/5",
                    )}
                    onClick={() => selectOption(currentQuestion, option.id)}
                  >
                    {currentQuestion.allow_multiple ? (
                      <Checkbox
                        checked={selected}
                        disabled={!interactive || submitting}
                        onCheckedChange={() => selectOption(currentQuestion, option.id)}
                        onClick={(event) => event.stopPropagation()}
                      />
                    ) : selected ? (
                      <CheckCircle2 className="text-primary size-4 shrink-0" />
                    ) : (
                      <Circle className="text-muted-foreground size-4 shrink-0" />
                    )}
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>
            {(currentQuestion.allow_custom ?? true) && (
              <div
                className={cn(
                  "border-border/70 hover:bg-muted/60 flex w-full items-start gap-2 rounded-lg border px-3 py-2 transition-colors",
                  currentCustomSelected && "border-primary/60 bg-primary/5",
                  (!interactive || submitting) && "opacity-60",
                )}
                onClick={() => selectCustom(currentQuestion)}
              >
                {currentQuestion.allow_multiple ? (
                  <Checkbox
                    checked={currentCustomSelected}
                    disabled={!interactive || submitting}
                    onCheckedChange={(checked) =>
                      checked ? selectCustom(currentQuestion) : deselectCustom(currentQuestion)
                    }
                    onClick={(event) => event.stopPropagation()}
                    className="mt-0.5"
                  />
                ) : currentCustomSelected ? (
                  <CheckCircle2 className="text-primary mt-0.5 size-4 shrink-0" />
                ) : (
                  <Circle className="text-muted-foreground mt-0.5 size-4 shrink-0" />
                )}
                <Textarea
                  value={customAnswers[currentQuestion.id] ?? ""}
                  disabled={!interactive || submitting}
                  placeholder={t("customPlaceholder")}
                  className="min-h-16 flex-1 border-0 bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
                  onChange={(event) => handleCustomChange(currentQuestion, event.target.value)}
                  onFocus={() => selectCustom(currentQuestion)}
                  onClick={(event) => event.stopPropagation()}
                />
              </div>
            )}
          </div>
        )}

        {interactive ? (
          <div className="flex items-center justify-between gap-2 pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={step === 0 || submitting}
              onClick={() => setStep((prev) => Math.max(0, prev - 1))}
            >
              <ChevronLeft className="size-4" />
              {t("prev")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={(!canAdvance && step < questions.length - 1) || (!canSubmit && step === questions.length - 1) || submitting}
              onClick={handleNext}
            >
              {step < questions.length - 1 ? (
                <>
                  {t("next")}
                  <ChevronRight className="size-4" />
                </>
              ) : submitting ? (
                t("submitting")
              ) : (
                t("submit")
              )}
            </Button>
          </div>
        ) : (
          <div className="text-muted-foreground text-xs">{t("submitted")}</div>
        )}
      </CardContent>
    </Card>
  );
}
