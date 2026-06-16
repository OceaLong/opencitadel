"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { fadeInUp, motion, reducedVariants } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { QuizResultView } from "./quiz-result-view";
import { scoreAnswers } from "./scoring";
import type { QuizBank } from "./types";
import { isLikertQuestion, LIKERT_LABELS_5 } from "./types";

type QuizEngineProps = {
  bank: QuizBank;
  onBack: () => void;
};

export function QuizEngine({ bank, onBack }: QuizEngineProps) {
  const reduced = usePrefersReducedMotion();
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, number | string>>({});
  const [result, setResult] = useState<ReturnType<QuizBank["computeResult"]> | null>(null);

  const question = bank.questions[step];
  const progress = ((step + 1) / bank.questions.length) * 100;
  const selected = question ? answers[question.id] : undefined;

  const shareUrl = useMemo(() => {
    if (!result || typeof window === "undefined") return "";
    const params = new URLSearchParams({ type: bank.id, code: result.code });
    return `${window.location.origin}/share/test?${params.toString()}`;
  }, [bank.id, result]);

  const handleLikertSelect = (value: number) => {
    if (!question) return;
    setAnswers((prev) => ({ ...prev, [question.id]: value }));
  };

  const handleChoiceSelect = (optionId: string) => {
    if (!question) return;
    setAnswers((prev) => ({ ...prev, [question.id]: optionId }));
  };

  const computeAndFinish = () => {
    const scores = scoreAnswers(bank, answers);
    setResult(bank.computeResult(scores));
  };

  const handleNext = () => {
    if (selected === undefined || selected === "") {
      toast.error("请选择一个选项");
      return;
    }
    if (step < bank.questions.length - 1) {
      setStep((s) => s + 1);
    } else {
      computeAndFinish();
    }
  };

  if (result) {
    return (
      <QuizResultView bank={bank} result={result} shareUrl={shareUrl} onBack={onBack} />
    );
  }

  return (
    <div className="mx-auto max-w-lg space-y-6 py-4">
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            {bank.icon} {bank.name}
          </span>
          <span className="text-muted-foreground">
            {step + 1} / {bank.questions.length}
          </span>
        </div>
        <Progress value={progress} className="h-2" />
      </div>

      <motion.div
        key={question?.id}
        initial="hidden"
        animate="visible"
        variants={reducedVariants(fadeInUp, reduced)}
      >
        <Card>
          <CardContent className="space-y-4 pt-6">
            <h3 className="text-foreground text-base leading-relaxed font-medium">
              {question?.text}
            </h3>

            {question && isLikertQuestion(question) ? (
              <div className="space-y-2">
                {LIKERT_LABELS_5.map((label, index) => {
                  const value = index + 1;
                  return (
                    <button
                      key={label}
                      type="button"
                      onClick={() => handleLikertSelect(value)}
                      className={cn(
                        "hover:border-primary/50 flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                        selected === value
                          ? "border-primary bg-primary/5 text-foreground"
                          : "border-border/70 bg-background text-muted-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                          selected === value
                            ? "border-primary bg-primary text-primary-foreground"
                            : "",
                        )}
                      >
                        {value}
                      </span>
                      {label}
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="space-y-2">
                {"options" in (question ?? {}) &&
                  question &&
                  "options" in question &&
                  question.options.map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => handleChoiceSelect(opt.id)}
                      className={cn(
                        "hover:border-primary/50 flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                        selected === opt.id
                          ? "border-primary bg-primary/5 text-foreground"
                          : "border-border/70 bg-background text-muted-foreground",
                      )}
                    >
                      {opt.text}
                    </button>
                  ))}
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      <div className="flex justify-between">
        <Button variant="ghost" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          <ChevronLeft className="mr-1 size-4" />
          上一题
        </Button>
        <Button onClick={handleNext}>
          {step < bank.questions.length - 1 ? (
            <>
              下一题
              <ChevronRight className="ml-1 size-4" />
            </>
          ) : (
            "查看结果"
          )}
        </Button>
      </div>
    </div>
  );
}
