'use client'

import { useState } from 'react'
import {
  Film,
  Loader2,
  Search,
  ExternalLink,
  ShieldCheck,
  X,
  Play,
  MonitorPlay,
} from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import type { VideoSearchData, VideoSearchResult } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { getVideoEmbed, isEmbeddableVideoUrl } from '@/components/marketplace/video-embed'

function VideoPlayerPanel({
  item,
  onClose,
}: {
  item: VideoSearchResult
  onClose: () => void
}) {
  const embed = getVideoEmbed(item.url)

  return (
    <Card className="overflow-hidden border-primary/20">
      <CardHeader className="py-3 px-4 border-b bg-muted/20">
        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="flex items-start gap-2 min-w-0 flex-1">
            <span className="text-lg shrink-0">{item.icon}</span>
            <div className="min-w-0">
              <CardTitle className="text-sm font-medium line-clamp-2">{item.title}</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">{item.platform}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 shrink-0">
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
          <div className="aspect-video w-full rounded-lg overflow-hidden border bg-black">
            <iframe
              src={embed.embedUrl}
              title={item.title}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
              className="w-full h-full"
            />
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed bg-muted/20 aspect-video px-4 text-center">
            <MonitorPlay className="size-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm font-medium text-foreground">{embed.reason}</p>
            <p className="text-xs text-muted-foreground mt-1 max-w-sm">
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
  )
}

export function VideoSearchApp() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<VideoSearchData | null>(null)
  const [searched, setSearched] = useState(false)
  const [active, setActive] = useState<VideoSearchResult | null>(null)

  const handleSearch = async () => {
    if (!query.trim()) {
      toast.error('请输入剧名')
      return
    }
    setLoading(true)
    setSearched(true)
    setActive(null)
    try {
      const result = await marketplaceApi.searchVideos({ query: query.trim() })
      setData(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '搜索失败')
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  const handleClear = () => {
    setQuery('')
    setData(null)
    setSearched(false)
    setActive(null)
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-gray-800">影视资源聚合搜索</h2>
        <p className="text-sm text-muted-foreground mt-1">
          聚合正版免费观看入口，支持中文/英文剧名模糊搜索
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex flex-col sm:flex-row gap-2">
          <div className="relative flex-1">
            <Input
              placeholder="输入剧名，如：三体、Breaking Bad"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="pr-9"
            />
            {query && (
              <button
                type="button"
                onClick={handleClear}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
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
        <p className="text-xs text-muted-foreground">按 Enter 键快速搜索</p>
      </div>

      {loading && (
        <div className="space-y-3">
          <Skeleton className="h-12 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
        </div>
      )}

      {!loading && !searched && (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 py-12 px-4 text-center">
          <Film className="size-10 text-muted-foreground/50 mb-3" />
          <p className="text-sm font-medium text-foreground">输入剧名开始搜索</p>
          <p className="text-xs text-muted-foreground mt-1 max-w-sm">
            支持中文、英文剧名，将为你聚合合规的正版免费观看入口
          </p>
        </div>
      )}

      {!loading && data && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/80 px-3 py-2.5 text-sm text-amber-900">
            <ShieldCheck className="size-4 shrink-0 mt-0.5" />
            <span>{data.copyright_notice}</span>
          </div>

          <div className="text-xs text-muted-foreground">
            已过滤风险来源 {data.stats.filtered_risk_sources} 个，展示合规结果{' '}
            {data.stats.legal_results} 个
          </div>

          {active && (
            <VideoPlayerPanel item={active} onClose={() => setActive(null)} />
          )}

          <div className="grid gap-2">
            {data.results.map((item) => {
              const embeddable = isEmbeddableVideoUrl(item.url)
              const isActive = active?.url === item.url

              return (
                <Card
                  key={item.url}
                  role="button"
                  tabIndex={0}
                  onClick={() => setActive(item)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setActive(item)
                    }
                  }}
                  className={cn(
                    'transition-colors cursor-pointer hover:border-primary/30 hover:bg-muted/20',
                    isActive && 'border-primary/50 bg-primary/5 ring-1 ring-primary/20'
                  )}
                >
                  <CardHeader className="py-3 px-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <CardTitle className="text-sm flex items-center gap-2 flex-wrap font-medium">
                          <span>{item.icon}</span>
                          <span className="truncate">{item.title}</span>
                        </CardTitle>
                        <div className="flex flex-wrap gap-1.5 mt-2">
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
                            <Badge className="text-[11px] bg-emerald-600 hover:bg-emerald-600">
                              可页内播放
                            </Badge>
                          )}
                        </div>
                      </div>
                      <div
                        className="flex flex-col sm:flex-row gap-1.5 shrink-0"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Button
                          size="sm"
                          onClick={() => setActive(item)}
                          className={cn(isActive && 'ring-2 ring-primary/30')}
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
              )
            })}
            {data.results.length === 0 && (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-10 text-center">
                <Film className="size-8 text-muted-foreground/40 mb-2" />
                <p className="text-sm text-muted-foreground">未找到合规观看入口</p>
                <p className="text-xs text-muted-foreground mt-1">试试换个关键词或英文剧名</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
