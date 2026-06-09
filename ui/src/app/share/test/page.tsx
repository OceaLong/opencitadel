"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { BANKS_BY_ID } from "@/components/marketplace/tests/banks";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

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
      <main className="flex flex-1 items-center justify-center p-6">
        <Card className="w-full max-w-md overflow-hidden">
          <div className="from-primary/20 via-violet-500/10 bg-gradient-to-br to-transparent px-6 py-8 text-center">
            <span className="text-5xl">{bank.icon}</span>
            <p className="text-muted-foreground mt-2 text-xs">{bank.name}</p>
            <h1 className="text-foreground mt-1 text-2xl font-bold">{result.title}</h1>
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
            <Button asChild className="w-full">
              <Link href="/marketplace">我也来测一测</Link>
            </Button>
          </CardContent>
        </Card>
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
