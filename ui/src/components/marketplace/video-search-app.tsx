"use client";

import { useEffect, useRef, useState } from "react";
import {
  ExternalLink,
  Film,
  Loader2,
  MonitorPlay,
  Play,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { getVideoEmbed, isEmbeddableVideoUrl } from "@/components/marketplace/video-embed";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

import { marketplaceApi } from "@/lib/api/marketplace";
import type { VideoSearchData, VideoSearchResult } from "@/lib/api/types";
import { cn } from "@/lib/utils";

function VideoPlayerPanel({ item, onClose }: { item: VideoSearchResult; onClose: () => void }) {
  const embed = getVideoEmbed(item.url);

  return (
    <Card className="border-primary/20 overflow-hidden">
      <CardHeader className="border-border/70 bg-muted/25 border-b px-4 py-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="flex min-w-0 flex-1 items-start gap-2">
            <span className="shrink-0 text-lg">{item.icon}</span>
            <div className="min-w-0">
              <CardTitle className="line-clamp-2 text-sm font-medium">{item.title}</CardTitle>
              <p className="text-muted-foreground mt-0.5 text-xs">{item.platform}</p>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button variant="outline" size="sm" asChild>
              <a href={item.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="size-3.5" />
                新标签打开
              </a>
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="size-3.5" />
              关闭
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-3 sm:p-4">
        {embed.embeddable ? (
          <div className="aspect-video w-full overflow-hidden rounded-lg border bg-black">
            <iframe
              src={embed.embedUrl}
              title={item.title}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
              className="h-full w-full"
            />
          </div>
        ) : (
          <div className="bg-muted/20 flex aspect-video flex-col items-center justify-center rounded-lg border border-dashed px-4 text-center">
            <MonitorPlay className="text-muted-foreground/40 mb-3 size-10" />
            <p className="text-foreground text-sm font-medium">{embed.reason}</p>
            <p className="text-muted-foreground mt-1 max-w-sm text-xs">
              该平台不允许页内嵌入，请在新标签中打开观看
            </p>
            <Button className="mt-4" asChild>
              <a href={item.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="size-4" />
                在新标签打开
              </a>
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function VideoSearchApp({
  initialQuery = "",
  autoRun = false,
}: {
  initialQuery?: string;
  autoRun?: boolean;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<VideoSearchData | null>(null);
  const [searched, setSearched] = useState(false);
  const [active, setActive] = useState<VideoSearchResult | null>(null);
  const autoRunRef = useRef(false);

  const handleSearch = async () => {
    if (!query.trim()) {
      toast.error("请输入剧名");
      return;
    }
    setLoading(true);
    setSearched(true);
    setActive(null);
    try {
      const result = await marketplaceApi.searchVideos({ query: query.trim() });
      setData(result);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "搜索失败");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!autoRun || autoRunRef.current || !initialQuery.trim()) return;
    autoRunRef.current = true;
    void handleSearch();
    // handleSearch intentionally reads the initial query set above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, initialQuery]);

  const handleClear = () => {
    setQuery("");
    setData(null);
    setSearched(false);
    setActive(null);
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">影视资源聚合搜索</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          聚合正版免费观看入口，支持中文/英文剧名模糊搜索
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Input
              placeholder="输入剧名，如：三体、Breaking Bad"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              className="pr-9"
            />
            {query && (
              <button
                type="button"
                onClick={handleClear}
                className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2"
                aria-label="清空搜索"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <Button onClick={handleSearch} disabled={loading} className="shrink-0">
            {loading ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
            搜索
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">按 Enter 键快速搜索</p>
      </div>

      {loading && (
        <div className="space-y-3">
          <Skeleton className="h-12 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
        </div>
      )}

      {!loading && !searched && (
        <div className="bg-muted/20 flex flex-col items-center justify-center rounded-xl border border-dashed px-4 py-12 text-center">
          <Film className="text-muted-foreground/50 mb-3 size-10" />
          <p className="text-foreground text-sm font-medium">输入剧名开始搜索</p>
          <p className="text-muted-foreground mt-1 max-w-sm text-xs">
            支持中文、英文剧名，将为你聚合合规的正版免费观看入口
          </p>
        </div>
      )}

      {!loading && data && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/80 px-3 py-2.5 text-sm text-amber-900">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            <span>{data.copyright_notice}</span>
          </div>

          <div className="text-muted-foreground text-xs">
            已过滤风险来源 {data.stats.filtered_risk_sources} 个，展示合规结果{" "}
            {data.stats.legal_results} 个
          </div>

          {active && <VideoPlayerPanel item={active} onClose={() => setActive(null)} />}

          <div className="grid gap-2">
            {data.results.map((item) => {
              const embeddable = isEmbeddableVideoUrl(item.url);
              const isActive = active?.url === item.url;

              return (
                <Card
                  key={item.url}
                  role="button"
                  tabIndex={0}
                  onClick={() => setActive(item)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setActive(item);
                    }
                  }}
                  className={cn(
                    "hover:border-primary/30 hover:bg-muted/30 cursor-pointer transition-all hover:shadow-[var(--shadow-card-hover)]",
                    isActive && "border-primary/50 bg-primary/5 ring-primary/20 ring-1",
                  )}
                >
                  <CardHeader className="px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <CardTitle className="flex flex-wrap items-center gap-2 text-sm font-medium">
                          <span>{item.icon}</span>
                          <span className="truncate">{item.title}</span>
                        </CardTitle>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Badge variant="secondary" className="text-[11px]">
                            {item.platform}
                          </Badge>
                          <Badge variant="outline" className="text-[11px]">
                            {item.condition}
                          </Badge>
                          <Badge variant="outline" className="text-[11px]">
                            {item.quality}
                          </Badge>
                          {embeddable && (
                            <Badge className="bg-emerald-600 text-[11px] hover:bg-emerald-600">
                              可页内播放
                            </Badge>
                          )}
                          {item.recommendation_reason && (
                            <Badge variant="secondary" className="text-[11px]">
                              {item.recommendation_reason}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <div
                        className="flex shrink-0 flex-col gap-1.5 sm:flex-row"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Button
                          size="sm"
                          onClick={() => setActive(item)}
                          className={cn(isActive && "ring-primary/30 ring-2")}
                        >
                          <Play className="size-3.5" />
                          播放
                        </Button>
                        <Button variant="outline" size="sm" asChild>
                          <a href={item.url} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="size-3.5" />
                            新标签
                          </a>
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                </Card>
              );
            })}
            {data.results.length === 0 && (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-10 text-center">
                <Film className="text-muted-foreground/40 mb-2 size-8" />
                <p className="text-muted-foreground text-sm">未找到合规观看入口</p>
                <p className="text-muted-foreground mt-1 text-xs">试试换个关键词或英文剧名</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
