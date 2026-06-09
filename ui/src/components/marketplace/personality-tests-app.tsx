"use client";

import { useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { ALL_BANKS } from "@/components/marketplace/tests/banks";
import { QuizEngine } from "@/components/marketplace/tests/quiz-engine";
import type { QuizBank } from "@/components/marketplace/tests/types";
import { cn } from "@/lib/utils";

type PersonalityTestsAppProps = {
  initialTestId?: string;
};

export function PersonalityTestsApp({ initialTestId }: PersonalityTestsAppProps) {
  const initial = initialTestId ? ALL_BANKS.find((b) => b.id === initialTestId) : null;
  const [active, setActive] = useState<QuizBank | null>(initial ?? null);

  if (active) {
    return <QuizEngine bank={active} onBack={() => setActive(null)} />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-foreground text-lg font-semibold">趣味人格测试</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          6 套经典测试，答题即可查看结果并分享给朋友
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {ALL_BANKS.map((bank) => (
          <button
            key={bank.id}
            type="button"
            onClick={() => setActive(bank)}
            className="text-left"
          >
            <Card
              className={cn(
                "hover:border-primary/40 h-full transition-colors",
                "cursor-pointer",
              )}
            >
              <CardContent className="space-y-2 pt-5">
                <span className="text-3xl">{bank.icon}</span>
                <h3 className="text-foreground text-sm font-semibold">{bank.name}</h3>
                <p className="text-muted-foreground line-clamp-2 text-xs">{bank.description}</p>
                <p className="text-muted-foreground text-[10px]">
                  {bank.questions.length} 道题 · 约 {Math.ceil(bank.questions.length * 0.5)} 分钟
                </p>
              </CardContent>
            </Card>
          </button>
        ))}
      </div>
    </div>
  );
}
