"use client";

import { useCallback, useEffect, useState } from "react";
import { LayoutGrid, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { AppCard } from "@/components/marketplace/app-card";
import { ConsumptionCalculatorApp } from "@/components/marketplace/consumption-calculator-app";
import { NutritionAnalysisApp } from "@/components/marketplace/nutrition-analysis-app";
import { VideoSearchApp } from "@/components/marketplace/video-search-app";
import { Skeleton } from "@/components/ui/skeleton";

import { marketplaceApi } from "@/lib/api/marketplace";
import type { MarketplaceApp } from "@/lib/api/types";

const FALLBACK_APPS: MarketplaceApp[] = [
  {
    id: "video-search",
    name: "影视资源聚合",
    description: "聚合正版免费观看入口，支持中文/英文剧名搜索",
    icon: "🎬",
    category: "娱乐",
  },
  {
    id: "nutrition-analysis",
    name: "AI营养分析",
    description: "拍照识别餐食营养，减脂/增肌红绿灯评估",
    icon: "🥗",
    category: "健康",
  },
  {
    id: "consumption-calculator",
    name: "消耗计算器",
    description: "识别包装净含量，计算可食用次数",
    icon: "📦",
    category: "生活",
  },
];

function renderApp(appId: string) {
  switch (appId) {
    case "video-search":
      return <VideoSearchApp />;
    case "nutrition-analysis":
      return <NutritionAnalysisApp />;
    case "consumption-calculator":
      return <ConsumptionCalculatorApp />;
    default:
      return (
        <div className="text-muted-foreground flex flex-col items-center justify-center py-16 text-center">
          <LayoutGrid className="mb-3 size-10 opacity-40" />
          <p className="text-sm">请从左侧选择一个应用开始使用</p>
        </div>
      );
  }
}

function AppListSkeleton({ compact }: { compact?: boolean }) {
  return (
    <div className={compact ? "flex gap-2 overflow-hidden" : "grid gap-2"}>
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton
          key={i}
          className={compact ? "h-24 w-[220px] shrink-0 rounded-xl" : "h-24 w-full rounded-xl"}
        />
      ))}
    </div>
  );
}

export function MarketplaceShell() {
  const [apps, setApps] = useState<MarketplaceApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string>("video-search");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await marketplaceApi.listApps();
      setApps(data.apps);
      if (data.apps.length > 0) {
        setActiveId(data.apps[0].id);
      }
    } catch {
      setApps(FALLBACK_APPS);
      toast.message("应用列表加载失败，已使用本地配置");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const displayApps = apps.length > 0 ? apps : FALLBACK_APPS;
  const activeApp = displayApps.find((app) => app.id === activeId);

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col gap-4 lg:flex-row lg:gap-6">
      <aside className="flex min-h-0 w-full shrink-0 flex-col lg:w-80 xl:w-72">
        <div className="mb-4 shrink-0">
          <h1 className="text-foreground text-xl font-semibold tracking-tight">应用市场助手</h1>
          <p className="text-muted-foreground mt-1 text-sm">免下载小应用，点击即可使用</p>
        </div>

        <div className="hidden min-h-0 flex-1 flex-col lg:flex">
          <p className="text-muted-foreground mb-2 shrink-0 text-xs font-medium tracking-wide uppercase">
            全部应用
          </p>
          <div className="-mr-1 min-h-0 flex-1 overflow-y-auto pr-1">
            {loading ? (
              <AppListSkeleton />
            ) : (
              <div className="grid gap-2 pb-2">
                {displayApps.map((app) => (
                  <AppCard
                    key={app.id}
                    app={app}
                    selected={activeId === app.id}
                    onClick={() => setActiveId(app.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 lg:hidden">
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            全部应用
          </p>
          {loading ? (
            <AppListSkeleton compact />
          ) : (
            <div className="-mx-1 flex snap-x snap-mandatory gap-2 overflow-x-auto px-1 pb-2">
              {displayApps.map((app) => (
                <AppCard
                  key={app.id}
                  app={app}
                  selected={activeId === app.id}
                  compact
                  onClick={() => setActiveId(app.id)}
                />
              ))}
            </div>
          )}
        </div>
      </aside>

      <main className="border-border/70 bg-card flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border shadow-[var(--shadow-card)]">
        {activeApp && (
          <div className="border-border/70 bg-muted/25 shrink-0 border-b px-4 py-3.5 sm:px-6">
            <div className="flex items-center gap-2">
              <span className="bg-background flex size-9 items-center justify-center rounded-xl text-lg shadow-[var(--shadow-card)]">
                {activeApp.icon}
              </span>
              <div>
                <h2 className="text-foreground text-sm font-semibold">{activeApp.name}</h2>
                <p className="text-muted-foreground line-clamp-1 text-xs">
                  {activeApp.description}
                </p>
              </div>
            </div>
          </div>
        )}
        <div className="flex-1 overflow-auto p-4 sm:p-6">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="text-muted-foreground size-6 animate-spin" />
            </div>
          ) : (
            renderApp(activeId)
          )}
        </div>
      </main>
    </div>
  );
}
