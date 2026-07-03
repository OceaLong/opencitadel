"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { cn } from "@/lib/utils";

type SuggestedQuestionsProps = {
  className?: string;
  onQuestionClick?: (question: string) => void;
};

export function SuggestedQuestions({ className, onQuestionClick }: SuggestedQuestionsProps) {
  const t = useTranslations("home");

  const handleClick = (question: string) => {
    onQuestionClick?.(question);
  };

  return (
    <div className={cn("flex flex-wrap gap-2 sm:gap-3", className)}>
      {(
        [
          t("suggestedQuestions.q1"),
          t("suggestedQuestions.q2"),
          t("suggestedQuestions.q3"),
          t("suggestedQuestions.q4"),
        ] as const
      ).map((question, index) => (
        <Button
          key={index}
            variant="outline"
            className="cursor-pointer text-xs break-words whitespace-normal sm:text-sm"
            onClick={() => handleClick(question)}
          >
          {question}
        </Button>
      ))}
    </div>
  );
}
