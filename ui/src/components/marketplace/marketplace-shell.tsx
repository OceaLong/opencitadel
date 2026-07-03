"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Home, Layers3, Loader2, Search, Sparkles, WandSparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { AppCard } from "@/components/marketplace/app-card";
import {
  FALLBACK_APPS,
  getCategoryLabel,
  localizeMarketplaceApp,
  type LaunchParams,
  renderApp,
} from "@/components/marketplace/app-registry";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

import { marketplaceApi } from "@/lib/api/marketplace";
import { isModelUnavailableStatus, llmStatusApi } from "@/lib/api/llm-status";
import type { LLMStatusData, MarketplaceApp } from "@/lib/api/types";
import { useRequireAuth } from "@/hooks/use-require-auth";
import { cn } from "@/lib/utils";

import {
  LEGACY_MARKETPLACE_RECENT_KEY,
  MARKETPLACE_RECENT_KEY,
} from "@/lib/storage-keys";
import { migrateLocalStorageKey, writeLocalStorageKey } from "@/lib/storage-migration";

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
  const t = useTranslations("marketplace");
  const tApps = useTranslations("marketplace.apps");
  const tAuth = useTranslations("auth");
  const { requireAuth } = useRequireAuth();
  const [apps, setApps] = useState<MarketplaceApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeParams, setActiveParams] = useState<LaunchParams>({});
  const [command, setCommand] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [recentIds, setRecentIds] = useState<string[]>([]);
  const [routing, setRouting] = useState(false);
  const [llmStatus, setLlmStatus] = useState<LLMStatusData["status"]>("unknown");
  const [offlineOnly, setOfflineOnly] = useState(false);

  const loadLlmStatus = useCallback(async () => {
    try {
      const data = await llmStatusApi.getStatus();
      setLlmStatus(data.status ?? "unknown");
    } catch {
      setLlmStatus("unknown");
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await marketplaceApi.listApps();
      setApps(data.apps);
    } catch {
      setApps(FALLBACK_APPS);
      toast.message(t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    load();
    void loadLlmStatus();
    const timer = setInterval(
      () => void loadLlmStatus(),
      isModelUnavailableStatus(llmStatus) ? 10_000 : 30_000,
    );
    return () => clearInterval(timer);
  }, [load, loadLlmStatus, llmStatus]);

  useEffect(() => {
    try {
      const raw = migrateLocalStorageKey(LEGACY_MARKETPLACE_RECENT_KEY, MARKETPLACE_RECENT_KEY);
      if (raw) setRecentIds(JSON.parse(raw));
    } catch {
      setRecentIds([]);
    }
  }, []);

  const localizedFallbackApps = useMemo(
    () => FALLBACK_APPS.map((app) => localizeMarketplaceApp(app, tApps)),
    [tApps],
  );

  const displayApps = useMemo(() => {
    const source = apps.length > 0 ? apps : localizedFallbackApps;
    return source.map((app) => localizeMarketplaceApp(app, tApps));
  }, [apps, localizedFallbackApps, tApps]);

  const activeApp = displayApps.find((app) => app.id === activeId);

  const categories = useMemo(
    () => ["all", ...Array.from(new Set((apps.length > 0 ? apps : FALLBACK_APPS).map((app) => app.category)))],
    [apps],
  );

  const filteredApps = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return displayApps.filter((app) => {
      const sourceApp = (apps.length > 0 ? apps : FALLBACK_APPS).find((item) => item.id === app.id);
      const categoryKey = sourceApp?.category ?? app.category;
      const categoryMatch = category === "all" || categoryKey === category;
      if (offlineOnly && (app.model_dependency ?? "optional") !== "none") {
        return false;
      }
      const blob = [app.name, app.description, getCategoryLabel(categoryKey, t), ...app.tags, ...app.examples]
        .join(" ")
        .toLowerCase();
      return categoryMatch && (!needle || blob.includes(needle));
    });
  }, [apps, category, displayApps, offlineOnly, query, t]);

  const modelUnavailable = isModelUnavailableStatus(llmStatus);

  const featuredApps = displayApps.filter((app) => app.featured).slice(0, 4);
  const recentApps = recentIds
    .map((id) => displayApps.find((app) => app.id === id))
    .filter((app): app is MarketplaceApp => Boolean(app));

  const remember = useCallback((appId: string) => {
    setRecentIds((prev) => {
      const next = [appId, ...prev.filter((id) => id !== appId)].slice(0, 6);
      writeLocalStorageKey(
        LEGACY_MARKETPLACE_RECENT_KEY,
        MARKETPLACE_RECENT_KEY,
        JSON.stringify(next),
      );
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
      toast.error(t("enterTask"));
      return;
    }
    if (!requireAuth(tAuth("loginToMarketplace"))) return;
    setRouting(true);
    try {
      const route = await marketplaceApi.routeRequest({ query: trimmed });
      openApp(route.app_id, route.params);
      toast.message(route.reason);
    } catch {
      const local = displayApps.find((app) =>
        [app.name, app.description, getCategoryLabel(app.category, t), ...app.tags, ...app.examples]
          .join(" ")
          .toLowerCase()
          .includes(trimmed.toLowerCase()),
      );
      if (local) {
        openApp(local.id);
        toast.message(t("localMatch"));
      } else {
        setQuery(trimmed);
        toast.message(t("switchedToSearch"));
      }
    } finally {
      setRouting(false);
    }
  }, [command, displayApps, openApp, requireAuth, t, tAuth]);

  const modelStatusLabel =
    llmStatus === "unknown"
      ? t("statusUnknown")
      : modelUnavailable
        ? t("unavailable")
        : llmStatus === "degraded"
          ? t("degraded")
          : t("normal");

  return (
    <div className="h-full min-h-0 overflow-hidden">
      {activeApp ? (
        <main className="border-border/70 bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-3xl border shadow-[var(--shadow-card)]">
          <div className="border-border/70 bg-muted/20 shrink-0 border-b px-4 py-3.5 sm:px-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-center gap-3">
                <Button variant="ghost" size="sm" onClick={() => setActiveId(null)}>
                  <ArrowLeft className="size-4" />
                  {t("backToMarket")}
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
        <div className="h-full overflow-auto rounded-2xl">
          <div className="mb-2">
            <Button variant="ghost" size="icon" className="size-8" asChild>
              <Link href="/" aria-label={t("backHome")}>
                <Home className="size-4" />
              </Link>
            </Button>
          </div>
          <section className="border-border/70 bg-card/80 relative overflow-hidden rounded-2xl border p-4 shadow-[var(--shadow-card)] sm:p-5">
            <div className="from-primary/20 via-sky-500/10 absolute inset-0 bg-gradient-to-br to-transparent" />
            <div className="relative max-w-3xl">
              <Badge className="bg-primary/10 text-primary hover:bg-primary/10 mb-2 text-[10px]">
                <Sparkles className="size-3" />
                {t("badge")}
              </Badge>
              <Badge
                variant={modelUnavailable ? "destructive" : "secondary"}
                className="mb-2 ml-1 text-[10px]"
              >
                {t("modelStatus")}
                {modelStatusLabel}
              </Badge>
              <h1 className="text-foreground text-xl font-semibold tracking-tight sm:text-2xl">
                {t("heroTitle")}
              </h1>
              <p className="text-muted-foreground mt-1.5 max-w-2xl text-xs leading-relaxed sm:text-sm">
                {t("heroDescription")}
              </p>
              <div className="mt-4 flex flex-col gap-1.5 rounded-xl border bg-background/80 p-1.5 shadow-[var(--shadow-card)] backdrop-blur sm:flex-row">
                <div className="relative flex-1">
                  <WandSparkles className="text-muted-foreground absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                  <Input
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && routeCommand()}
                    placeholder={t("commandPlaceholder")}
                    className="h-8 border-0 bg-transparent pl-8 text-sm shadow-none focus-visible:ring-0"
                  />
                </div>
                <Button size="sm" onClick={routeCommand} disabled={routing} className="shrink-0">
                  {routing ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
                  {t("smartLaunch")}
                </Button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {displayApps
                  .flatMap((app) => app.examples)
                  .slice(0, 4)
                  .map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => setCommand(example)}
                      className="text-muted-foreground hover:text-foreground rounded-full border bg-background/70 px-2.5 py-0.5 text-[11px] transition-colors"
                    >
                      {example}
                    </button>
                  ))}
              </div>
            </div>
          </section>

          <div className="mt-4 space-y-5 pb-2">
            {loading ? (
              <AppListSkeleton />
            ) : (
              <>
                <section className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h2 className="text-foreground text-base font-semibold">{t("featured")}</h2>
                      <p className="text-muted-foreground text-xs">{t("featuredDesc")}</p>
                    </div>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    {featuredApps.map((app) => (
                      <AppCard
                        key={app.id}
                        app={app}
                        wide
                        modelUnavailable={modelUnavailable}
                        onClick={() => openApp(app.id)}
                      />
                    ))}
                  </div>
                </section>

                {recentApps.length > 0 && (
                  <section className="space-y-2">
                    <h2 className="text-foreground text-base font-semibold">{t("recent")}</h2>
                    <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
                      {recentApps.map((app) => (
                        <AppCard
                          key={app.id}
                          app={app}
                          compact
                          modelUnavailable={modelUnavailable}
                          onClick={() => openApp(app.id)}
                        />
                      ))}
                    </div>
                  </section>
                )}

                <section className="space-y-3">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                      <h2 className="text-foreground text-lg font-semibold">{t("allApps")}</h2>
                      <p className="text-muted-foreground text-sm">{t("allAppsDesc")}</p>
                    </div>
                    <div className="relative w-full lg:w-80">
                      <Search className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                      <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder={t("searchPlaceholder")}
                        className="pl-9"
                      />
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 overflow-x-auto pb-1">
                    {categories.map((item) => (
                      <Button
                        key={item}
                        size="sm"
                        variant={category === item ? "default" : "outline"}
                        onClick={() => setCategory(item)}
                        className={cn("shrink-0 rounded-full", category !== item && "bg-background/70")}
                      >
                        {getCategoryLabel(item, t)}
                      </Button>
                    ))}
                    <Button
                      size="sm"
                      variant={offlineOnly ? "default" : "outline"}
                      onClick={() => setOfflineOnly((v) => !v)}
                      className={cn("shrink-0 rounded-full", !offlineOnly && "bg-background/70")}
                    >
                      {t("offlineOnly")}
                    </Button>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {filteredApps.map((app) => (
                      <AppCard
                        key={app.id}
                        app={app}
                        wide
                        modelUnavailable={modelUnavailable}
                        onClick={() => openApp(app.id)}
                      />
                    ))}
                  </div>
                  {filteredApps.length === 0 && (
                    <div className="border-border/70 bg-muted/20 flex flex-col items-center justify-center rounded-2xl border border-dashed px-4 py-12 text-center">
                      <Layers3 className="text-muted-foreground/50 mb-3 size-9" />
                      <p className="text-foreground text-sm font-medium">{t("noMatch")}</p>
                      <p className="text-muted-foreground mt-1 text-xs">{t("noMatchHint")}</p>
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
