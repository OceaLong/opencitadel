"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";

import { FortuneResultView } from "@/components/marketplace/fortune/fortune-result-view";
import { Button } from "@/components/ui/button";

import { marketplaceApi } from "@/lib/api/marketplace";
import type { FortunePredictionData } from "@/lib/api/types";

function ShareFortuneContent() {
  const params = useParams();
  const shareId = typeof params.id === "string" ? params.id : "";
  const [data, setData] = useState<FortunePredictionData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shareId) {
      setError("分享链接无效");
      setLoading(false);
      return;
    }
    void (async () => {
      try {
        const result = await marketplaceApi.getFortuneShare(shareId);
        setData(result);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [shareId]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="text-muted-foreground size-8 animate-spin" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <p className="text-muted-foreground text-sm">{error || "未找到该预测结果"}</p>
        <Button asChild variant="outline">
          <Link href="/marketplace">去应用市场体验</Link>
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
        <span className="text-muted-foreground text-sm">运势预测分享</span>
      </header>
      <main className="flex flex-1 justify-center p-6">
        <div className="w-full max-w-lg">
          <FortuneResultView data={data} showReset={false} />
          <div className="mt-4 text-center">
            <Button asChild className="w-full">
              <Link href="/marketplace">我也来测一测</Link>
            </Button>
          </div>
        </div>
      </main>
    </div>
  );
}

export default function ShareFortunePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Loader2 className="text-muted-foreground size-8 animate-spin" />
        </div>
      }
    >
      <ShareFortuneContent />
    </Suspense>
  );
}
