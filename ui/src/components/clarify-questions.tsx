"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, ChevronLeft, ChevronRight, Circle, HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";

import type { ClarifyAnswer, ClarifyQuestion } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type ClarifyQuestionsProps = {
  title?: string | null;
  questions: ClarifyQuestion[];
  interactive: boolean;
  onSubmit?: (answer: string, structuredAnswers: ClarifyAnswer[]) => Promise<void> | void;
  className?: string;
};

function isQuestionAnswered(
  question: ClarifyQuestion,
  selections: Record<string, string[]>,
  customAnswers: Record<string, string>,
) {
  const selected = selections[question.id] ?? [];
  const custom = (customAnswers[question.id] ?? "").trim();
  return selected.length > 0 || custom.length > 0;
}

export function ClarifyQuestions({
  title,
  questions,
  interactive,
  onSubmit,
  className,
}: ClarifyQuestionsProps) {
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

  const toggleOption = (question: ClarifyQuestion, optionId: string) => {
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

  const buildAnswer = () => {
    const lines = ["【澄清回复】"];
    for (const question of questions) {
      const selectedIds = selections[question.id] ?? [];
      const selectedLabels = question.options
        .filter((option) => selectedIds.includes(option.id))
        .map((option) => option.label);
      const custom = (customAnswers[question.id] ?? "").trim();
      const parts = [...selectedLabels];
      if (custom) parts.push(`自定义: ${custom}`);
      lines.push(`- ${question.prompt}: ${parts.join("；")}`);
    }
    return lines.join("\n");
  };

  const buildStructuredAnswers = (): ClarifyAnswer[] => {
    return questions.map((question) => {
      const selectedIds = selections[question.id] ?? [];
      const selectedLabels = question.options
        .filter((option) => selectedIds.includes(option.id))
        .map((option) => option.label);
      const custom = (customAnswers[question.id] ?? "").trim();
      return {
        question_id: question.id,
        prompt: question.prompt,
        option_ids: selectedIds,
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

  return (
    <Card className={cn("gap-4 py-4", className)}>
      <CardHeader className="gap-2 px-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <HelpCircle className="text-primary size-4" />
          <span>{title || "需要确认几个关键点"}</span>
        </CardTitle>
        {interactive && questions.length > 1 && (
          <div className="space-y-2">
            <div className="text-muted-foreground flex items-center justify-between text-xs">
              <span>
                问题 {step + 1} / {questions.length}
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
                const selected = (selections[currentQuestion.id] ?? []).includes(option.id);
                return (
                  <button
                    key={option.id}
                    type="button"
                    disabled={!interactive || submitting}
                    className={cn(
                      "border-border/70 hover:bg-muted/60 flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                      selected && "border-primary/60 bg-primary/5",
                    )}
                    onClick={() => toggleOption(currentQuestion, option.id)}
                  >
                    {currentQuestion.allow_multiple ? (
                      <Checkbox
                        checked={selected}
                        disabled={!interactive || submitting}
                        onCheckedChange={() => toggleOption(currentQuestion, option.id)}
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
              <Textarea
                value={customAnswers[currentQuestion.id] ?? ""}
                disabled={!interactive || submitting}
                placeholder="其它 / 自定义回答..."
                className="min-h-16 text-sm"
                onChange={(event) =>
                  setCustomAnswers((prev) => ({
                    ...prev,
                    [currentQuestion.id]: event.target.value,
                  }))
                }
              />
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
              上一题
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={(!canAdvance && step < questions.length - 1) || (!canSubmit && step === questions.length - 1) || submitting}
              onClick={handleNext}
            >
              {step < questions.length - 1 ? (
                <>
                  下一题
                  <ChevronRight className="size-4" />
                </>
              ) : submitting ? (
                "提交中..."
              ) : (
                "提交回答"
              )}
            </Button>
          </div>
        ) : (
          <div className="text-muted-foreground text-xs">已提交回答，继续处理中。</div>
        )}
      </CardContent>
    </Card>
  );
}
