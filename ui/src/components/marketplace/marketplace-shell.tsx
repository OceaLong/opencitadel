'use client'

import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import type { MarketplaceApp } from '@/lib/api/types'
import { AppCard } from '@/components/marketplace/app-card'
import { VideoSearchApp } from '@/components/marketplace/video-search-app'
import { NutritionAnalysisApp } from '@/components/marketplace/nutrition-analysis-app'
import { ConsumptionCalculatorApp } from '@/components/marketplace/consumption-calculator-app'

const FALLBACK_APPS: MarketplaceApp[] = [
  {
    id: 'video-search',
    name: '影视资源聚合',
    description: '聚合正版免费观看入口，支持中文/英文剧名搜索',
    icon: '🎬',
    category: '娱乐',
  },
  {
    id: 'nutrition-analysis',
    name: 'AI营养分析',
    description: '拍照识别餐食营养，减脂/增肌红绿灯评估',
    icon: '🥗',
    category: '健康',
  },
  {
    id: 'consumption-calculator',
    name: '消耗计算器',
    description: '识别包装净含量，计算可食用次数',
    icon: '📦',
    category: '生活',
  },
]

function renderApp(appId: string) {
  switch (appId) {
    case 'video-search':
      return <VideoSearchApp />
    case 'nutrition-analysis':
      return <NutritionAnalysisApp />
    case 'consumption-calculator':
      return <ConsumptionCalculatorApp />
    default:
      return <p className="text-muted-foreground text-sm">请选择一个应用</p>
  }
}

export function MarketplaceShell() {
  const [apps, setApps] = useState<MarketplaceApp[]>([])
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState<string>('video-search')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await marketplaceApi.listApps()
      setApps(data.apps)
      if (data.apps.length > 0) {
        setActiveId(data.apps[0].id)
      }
    } catch {
      setApps(FALLBACK_APPS)
      toast.message('应用列表加载失败，已使用本地配置')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const displayApps = apps.length > 0 ? apps : FALLBACK_APPS

  return (
    <div className="flex flex-col lg:flex-row gap-6 min-h-0 flex-1">
      <aside className="w-full lg:w-72 shrink-0 space-y-3">
        <div>
          <h1 className="text-xl font-bold text-gray-700">应用市场助手</h1>
          <p className="text-sm text-muted-foreground mt-1">
            免下载小应用，点击即可使用
          </p>
        </div>
        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="size-5 animate-spin" />
          </div>
        ) : (
          <div className="grid gap-2">
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
      </aside>

      <main className="flex-1 min-w-0 rounded-xl border bg-white p-4 sm:p-6 overflow-auto">
        {renderApp(activeId)}
      </main>
    </div>
  )
}
