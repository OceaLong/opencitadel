"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, Circle, HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";

import type { ClarifyQuestion } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type ClarifyQuestionsProps = {
  title?: string | null;
  questions: ClarifyQuestion[];
  interactive: boolean;
  onSubmit?: (answer: string) => Promise<void> | void;
  className?: string;
};

export function ClarifyQuestions({
  title,
  questions,
  interactive,
  onSubmit,
  className,
}: ClarifyQuestionsProps) {
  const [selections, setSelections] = useState<Record<string, string[]>>({});
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const canSubmit = useMemo(() => {
    if (!interactive || questions.length === 0) return false;
    return questions.every((question) => {
      const selected = selections[question.id] ?? [];
      const custom = (customAnswers[question.id] ?? "").trim();
      return selected.length > 0 || custom.length > 0;
    });
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

  const handleSubmit = async () => {
    if (!canSubmit || !onSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit(buildAnswer());
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className={cn("gap-4 py-4", className)}>
      <CardHeader className="gap-1 px-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <HelpCircle className="text-primary size-4" />
          <span>{title || "需要确认几个关键点"}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 px-4">
        {questions.map((question, index) => (
          <div key={question.id} className="space-y-2">
            <div className="text-foreground text-sm font-medium">
              {index + 1}. {question.prompt}
            </div>
            <div className="flex flex-col gap-2">
              {question.options.map((option) => {
                const selected = (selections[question.id] ?? []).includes(option.id);
                return (
                  <button
                    key={option.id}
                    type="button"
                    disabled={!interactive || submitting}
                    className={cn(
                      "border-border/70 hover:bg-muted/60 flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                      selected && "border-primary/60 bg-primary/5",
                    )}
                    onClick={() => toggleOption(question, option.id)}
                  >
                    {question.allow_multiple ? (
                      <Checkbox
                        checked={selected}
                        disabled={!interactive || submitting}
                        onCheckedChange={() => toggleOption(question, option.id)}
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
            {(question.allow_custom ?? true) && (
              <Textarea
                value={customAnswers[question.id] ?? ""}
                disabled={!interactive || submitting}
                placeholder="其它 / 自定义回答..."
                className="min-h-16 text-sm"
                onChange={(event) =>
                  setCustomAnswers((prev) => ({
                    ...prev,
                    [question.id]: event.target.value,
                  }))
                }
              />
            )}
          </div>
        ))}
        {interactive ? (
          <Button
            type="button"
            size="sm"
            disabled={!canSubmit || submitting}
            onClick={handleSubmit}
          >
            提交回答
          </Button>
        ) : (
          <div className="text-muted-foreground text-xs">已提交回答，继续处理中。</div>
        )}
      </CardContent>
    </Card>
  );
}
