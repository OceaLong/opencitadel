"use client";

import { useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Copy, Share2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

import type { QuizBank, QuizResult } from "./types";

type QuizEngineProps = {
  bank: QuizBank;
  onBack: () => void;
};

export function QuizEngine({ bank, onBack }: QuizEngineProps) {
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<QuizResult | null>(null);

  const question = bank.questions[step];
  const progress = ((step + 1) / bank.questions.length) * 100;
  const selected = question ? answers[question.id] : undefined;

  const shareUrl = useMemo(() => {
    if (!result || typeof window === "undefined") return "";
    const params = new URLSearchParams({ type: bank.id, code: result.code });
    return `${window.location.origin}/share/test?${params.toString()}`;
  }, [bank.id, result]);

  const handleSelect = (optionId: string) => {
    if (!question) return;
    setAnswers((prev) => ({ ...prev, [question.id]: optionId }));
  };

  const computeAndFinish = () => {
    const scores: Record<string, number> = {};
    for (const q of bank.questions) {
      const optId = answers[q.id];
      const opt = q.options.find((o) => o.id === optId);
      if (opt) {
        for (const [dim, w] of Object.entries(opt.weights)) {
          scores[dim] = (scores[dim] ?? 0) + w;
        }
      }
    }
    setResult(bank.computeResult(scores));
  };

  const handleNext = () => {
    if (!selected) {
      toast.error("请选择一个选项");
      return;
    }
    if (step < bank.questions.length - 1) {
      setStep((s) => s + 1);
    } else {
      computeAndFinish();
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      toast.success("分享链接已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  if (result) {
    return (
      <div className="mx-auto max-w-lg space-y-6 py-4">
        <Card className="overflow-hidden">
          <div className="from-primary/20 via-violet-500/10 bg-gradient-to-br to-transparent px-6 py-8 text-center">
            <span className="text-5xl">{bank.icon}</span>
            <p className="text-muted-foreground mt-2 text-xs">{bank.name}</p>
            <h2 className="text-foreground mt-1 text-2xl font-bold">{result.title}</h2>
            <p className="text-primary mt-1 text-sm font-medium">{result.code}</p>
          </div>
          <CardContent className="space-y-4 pt-6">
            <p className="text-muted-foreground text-sm leading-relaxed">{result.description}</p>
            <div className="flex flex-wrap gap-2">
              {result.traits.map((t) => (
                <span
                  key={t}
                  className="bg-primary/10 text-primary rounded-full px-3 py-1 text-xs font-medium"
                >
                  {t}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={handleCopy}>
            <Copy className="mr-1 size-4" />
            复制分享链接
          </Button>
          <Button variant="outline" onClick={() => window.open(shareUrl, "_blank")}>
            <Share2 className="mr-1 size-4" />
            打开分享页
          </Button>
          <Button variant="ghost" onClick={onBack}>
            返回测试列表
          </Button>
        </div>
      </div>
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

      <Card>
        <CardContent className="space-y-4 pt-6">
          <h3 className="text-foreground text-base font-medium leading-relaxed">
            {question?.text}
          </h3>
          <div className="space-y-2">
            {question?.options.map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => handleSelect(opt.id)}
                className={cn(
                  "hover:border-primary/50 flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                  selected === opt.id
                    ? "border-primary bg-primary/5 text-foreground"
                    : "border-border/70 bg-background text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex size-5 shrink-0 items-center justify-center rounded-full border",
                    selected === opt.id ? "border-primary bg-primary text-primary-foreground" : "",
                  )}
                >
                  {selected === opt.id && <Check className="size-3" />}
                </span>
                {opt.text}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-between">
        <Button
          variant="ghost"
          disabled={step === 0}
          onClick={() => setStep((s) => s - 1)}
        >
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
