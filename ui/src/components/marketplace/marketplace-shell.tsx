"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Layers3, Loader2, Search, Sparkles, WandSparkles } from "lucide-react";
import { toast } from "sonner";

import { AppCard } from "@/components/marketplace/app-card";
import {
  FALLBACK_APPS,
  type LaunchParams,
  renderApp,
} from "@/components/marketplace/app-registry";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

import { marketplaceApi } from "@/lib/api/marketplace";
import type { MarketplaceApp } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const RECENT_KEY = "my-manus-marketplace-recent";

function AppListSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-44 rounded-2xl" />
      ))}
    </div>
  );
}

export function MarketplaceShell() {
  const [apps, setApps] = useState<MarketplaceApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeParams, setActiveParams] = useState<LaunchParams>({});
  const [command, setCommand] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("全部");
  const [recentIds, setRecentIds] = useState<string[]>([]);
  const [routing, setRouting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await marketplaceApi.listApps();
      setApps(data.apps);
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

  useEffect(() => {
    try {
      const raw = localStorage.getItem(RECENT_KEY);
      if (raw) setRecentIds(JSON.parse(raw));
    } catch {
      setRecentIds([]);
    }
  }, []);

  const displayApps = apps.length > 0 ? apps : FALLBACK_APPS;
  const activeApp = displayApps.find((app) => app.id === activeId);

  const categories = useMemo(
    () => ["全部", ...Array.from(new Set(displayApps.map((app) => app.category)))],
    [displayApps],
  );

  const filteredApps = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return displayApps.filter((app) => {
      const categoryMatch = category === "全部" || app.category === category;
      const blob = [app.name, app.description, app.category, ...app.tags, ...app.examples]
        .join(" ")
        .toLowerCase();
      return categoryMatch && (!needle || blob.includes(needle));
    });
  }, [category, displayApps, query]);

  const featuredApps = displayApps.filter((app) => app.featured).slice(0, 4);
  const recentApps = recentIds
    .map((id) => displayApps.find((app) => app.id === id))
    .filter((app): app is MarketplaceApp => Boolean(app));

  const remember = useCallback((appId: string) => {
    setRecentIds((prev) => {
      const next = [appId, ...prev.filter((id) => id !== appId)].slice(0, 6);
      localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const openApp = useCallback(
    (appId: string, params: LaunchParams = {}) => {
      setActiveId(appId);
      setActiveParams(params);
      remember(appId);
    },
    [remember],
  );

  const routeCommand = useCallback(async () => {
    const trimmed = command.trim();
    if (!trimmed) {
      toast.error("请输入想完成的任务");
      return;
    }
    setRouting(true);
    try {
      const route = await marketplaceApi.routeRequest({ query: trimmed });
      openApp(route.app_id, route.params);
      toast.message(route.reason);
    } catch {
      const local = displayApps.find((app) =>
        [app.name, app.description, app.category, ...app.tags, ...app.examples]
          .join(" ")
          .toLowerCase()
          .includes(trimmed.toLowerCase()),
      );
      if (local) {
        openApp(local.id);
        toast.message("已根据本地匹配打开应用");
      } else {
        setQuery(trimmed);
        toast.message("已切换为应用搜索");
      }
    } finally {
      setRouting(false);
    }
  }, [command, displayApps, openApp]);

  return (
    <div className="h-full min-h-0 overflow-hidden">
      {activeApp ? (
        <main className="border-border/70 bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-3xl border shadow-[var(--shadow-card)]">
          <div className="border-border/70 bg-muted/20 shrink-0 border-b px-4 py-3.5 sm:px-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-center gap-3">
                <Button variant="ghost" size="sm" onClick={() => setActiveId(null)}>
                  <ArrowLeft className="size-4" />
                  返回市场
                </Button>
                <span className="bg-background flex size-10 items-center justify-center rounded-2xl text-xl shadow-[var(--shadow-card)]">
                  {activeApp.icon}
                </span>
                <div className="min-w-0">
                  <h2 className="text-foreground text-sm font-semibold">{activeApp.name}</h2>
                  <p className="text-muted-foreground line-clamp-1 text-xs">
                    {activeApp.description}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {activeApp.tags.slice(0, 4).map((tag) => (
                  <Badge key={tag} variant="secondary" className="text-[10px]">
                    {tag}
                  </Badge>
                ))}
              </div>
            </div>
          </div>
          <div className="flex-1 overflow-auto p-4 sm:p-6">
            {renderApp(activeApp.id, activeParams)}
          </div>
        </main>
      ) : (
        <div className="h-full overflow-auto rounded-3xl">
          <section className="border-border/70 bg-card/80 relative overflow-hidden rounded-3xl border p-5 shadow-[var(--shadow-card)] sm:p-8">
            <div className="from-primary/20 via-sky-500/10 absolute inset-0 bg-gradient-to-br to-transparent" />
            <div className="relative max-w-3xl">
              <Badge className="bg-primary/10 text-primary hover:bg-primary/10 mb-4">
                <Sparkles className="size-3.5" />
                AI Native Marketplace
              </Badge>
              <h1 className="text-foreground text-3xl font-semibold tracking-tight sm:text-4xl">
                说出目标，直接启动最合适的智能应用
              </h1>
              <p className="text-muted-foreground mt-3 max-w-2xl text-sm leading-relaxed sm:text-base">
                搜索、视觉分析、文档问答、翻译与提示词优化集中在一个轻量应用市场中。
              </p>
              <div className="mt-6 flex flex-col gap-2 rounded-2xl border bg-background/80 p-2 shadow-[var(--shadow-card)] backdrop-blur sm:flex-row">
                <div className="relative flex-1">
                  <WandSparkles className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                  <Input
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && routeCommand()}
                    placeholder="想做什么？例如：搜索三体 / 分析餐食 / 翻译这段英文"
                    className="border-0 bg-transparent pl-9 shadow-none focus-visible:ring-0"
                  />
                </div>
                <Button onClick={routeCommand} disabled={routing} className="shrink-0">
                  {routing ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                  智能启动
                </Button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {displayApps
                  .flatMap((app) => app.examples)
                  .slice(0, 4)
                  .map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => setCommand(example)}
                      className="text-muted-foreground hover:text-foreground rounded-full border bg-background/70 px-3 py-1 text-xs transition-colors"
                    >
                      {example}
                    </button>
                  ))}
              </div>
            </div>
          </section>

          <div className="mt-6 space-y-8 pb-3">
            {loading ? (
              <AppListSkeleton />
            ) : (
              <>
                <section className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h2 className="text-foreground text-lg font-semibold">精选应用</h2>
                      <p className="text-muted-foreground text-sm">高频 AI 小应用，点击即用</p>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {featuredApps.map((app) => (
                      <AppCard key={app.id} app={app} wide onClick={() => openApp(app.id)} />
                    ))}
                  </div>
                </section>

                {recentApps.length > 0 && (
                  <section className="space-y-3">
                    <h2 className="text-foreground text-lg font-semibold">猜你想用</h2>
                    <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-1">
                      {recentApps.map((app) => (
                        <AppCard
                          key={app.id}
                          app={app}
                          compact
                          onClick={() => openApp(app.id)}
                        />
                      ))}
                    </div>
                  </section>
                )}

                <section className="space-y-3">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                      <h2 className="text-foreground text-lg font-semibold">全部应用</h2>
                      <p className="text-muted-foreground text-sm">按分类或关键词快速发现能力</p>
                    </div>
                    <div className="relative w-full lg:w-80">
                      <Search className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                      <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="搜索应用、标签或场景"
                        className="pl-9"
                      />
                    </div>
                  </div>

                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {categories.map((item) => (
                      <Button
                        key={item}
                        size="sm"
                        variant={category === item ? "default" : "outline"}
                        onClick={() => setCategory(item)}
                        className={cn("shrink-0 rounded-full", category !== item && "bg-background/70")}
                      >
                        {item}
                      </Button>
                    ))}
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {filteredApps.map((app) => (
                      <AppCard key={app.id} app={app} wide onClick={() => openApp(app.id)} />
                    ))}
                  </div>
                  {filteredApps.length === 0 && (
                    <div className="border-border/70 bg-muted/20 flex flex-col items-center justify-center rounded-2xl border border-dashed px-4 py-12 text-center">
                      <Layers3 className="text-muted-foreground/50 mb-3 size-9" />
                      <p className="text-foreground text-sm font-medium">没有找到匹配应用</p>
                      <p className="text-muted-foreground mt-1 text-xs">试试换个关键词或使用上方 AI 指令栏</p>
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
