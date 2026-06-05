'use client'

import { useState } from 'react'
import { Loader2, Search, ExternalLink, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import type { VideoSearchData } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'

export function VideoSearchApp() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<VideoSearchData | null>(null)

  const handleSearch = async () => {
    if (!query.trim()) {
      toast.error('请输入剧名')
      return
    }
    setLoading(true)
    try {
      const result = await marketplaceApi.searchVideos({ query: query.trim() })
      setData(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '搜索失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold text-gray-700">影视资源聚合搜索</h2>
        <p className="text-sm text-muted-foreground mt-1">
          聚合正版免费观看入口，支持中文/英文剧名模糊搜索
        </p>
      </div>

      <div className="flex gap-2">
        <Input
          placeholder="输入剧名，如：三体、Breaking Bad"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
        <Button onClick={handleSearch} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
          搜索
        </Button>
      </div>

      {data && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-lg border bg-amber-50/80 px-3 py-2 text-sm text-amber-900">
            <ShieldCheck className="size-4 shrink-0 mt-0.5" />
            <span>{data.copyright_notice}</span>
          </div>

          <div className="text-xs text-muted-foreground">
            已过滤风险来源 {data.stats.filtered_risk_sources} 个，展示合规结果 {data.stats.legal_results} 个
          </div>

          <div className="grid gap-2">
            {data.results.map((item) => (
              <Card key={item.url}>
                <CardHeader className="py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
                        <span>{item.icon}</span>
                        <span className="truncate">{item.title}</span>
                      </CardTitle>
                      <div className="flex flex-wrap gap-1.5 mt-2">
                        <Badge variant="secondary">{item.platform}</Badge>
                        <Badge variant="outline">{item.condition}</Badge>
                        <Badge variant="outline">{item.quality}</Badge>
                      </div>
                    </div>
                    <Button variant="outline" size="sm" asChild>
                      <a href={item.url} target="_blank" rel="noopener noreferrer">
                        <ExternalLink className="size-3.5" />
                        直达
                      </a>
                    </Button>
                  </div>
                </CardHeader>
              </Card>
            ))}
            {data.results.length === 0 && (
              <p className="text-center text-muted-foreground text-sm py-8">未找到合规观看入口</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
