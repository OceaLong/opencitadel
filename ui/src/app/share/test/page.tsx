"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { BANKS_BY_ID } from "@/components/marketplace/tests/banks";
import { QuizResultView } from "@/components/marketplace/tests/quiz-result-view";
import { Button } from "@/components/ui/button";

function ShareTestContent() {
  const params = useSearchParams();
  const type = params.get("type") ?? "";
  const code = params.get("code") ?? "";

  const bank = BANKS_BY_ID.get(type);
  const result = bank?.results[code];

  if (!bank || !result) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <p className="text-muted-foreground text-sm">未找到该测试结果</p>
        <Button asChild variant="outline">
          <Link href="/marketplace">去应用市场测试</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="from-background via-background to-muted/30 flex min-h-screen flex-col bg-gradient-to-br">
      <header className="border-border/70 bg-background/70 flex shrink-0 items-center gap-4 border-b px-4 py-3 backdrop-blur">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/marketplace">
            <ArrowLeft className="mr-1 size-4" />
            应用市场
          </Link>
        </Button>
        <span className="text-muted-foreground text-sm">测试结果分享</span>
      </header>
      <main className="flex flex-1 justify-center p-6">
        <div className="w-full max-w-lg">
          <QuizResultView bank={bank} result={result} showActions={false} />
          <Button asChild className="mt-4 w-full">
            <Link href="/marketplace">我也来测一测</Link>
          </Button>
        </div>
      </main>
    </div>
  );
}

export default function ShareTestPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center">加载中...</div>}>
      <ShareTestContent />
    </Suspense>
  );
}
