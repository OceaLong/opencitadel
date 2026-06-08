'use client'

import { useState } from 'react'
import { Salad } from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import { fileApi } from '@/lib/api/file'
import type { NutritionAnalysisData } from '@/lib/api/types'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { TrafficLight } from '@/components/marketplace/traffic-light'
import { ImageUploadZone } from '@/components/marketplace/image-upload-zone'
import { Skeleton } from '@/components/ui/skeleton'

const MAX_SIZE = 5 * 1024 * 1024
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/png']

function MetricCard({ label, value, unit }: { label: string; value: number; unit: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 px-3 py-2.5 text-center">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-base font-semibold text-foreground mt-0.5">
        {value}
        <span className="text-xs font-normal text-muted-foreground ml-0.5">{unit}</span>
      </p>
    </div>
  )
}

export function NutritionAnalysisApp() {
  const [preview, setPreview] = useState<string | null>(null)
  const [weightKg, setWeightKg] = useState('')
  const [goal, setGoal] = useState<string>('maintain')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<NutritionAnalysisData | null>(null)

  const handleFile = async (file: File) => {
    if (!ALLOWED_TYPES.includes(file.type)) {
      toast.error('仅支持 JPG/PNG 图片')
      return
    }
    if (file.size > MAX_SIZE) {
      toast.error('图片不能超过 5MB')
      return
    }

    setPreview(URL.createObjectURL(file))
    setLoading(true)
    setResult(null)

    try {
      const uploaded = await fileApi.uploadFile({ file })
      const data = await marketplaceApi.analyzeNutrition({
        file_id: uploaded.id,
        weight_kg: weightKg ? Number(weightKg) : undefined,
        goal: goal as 'cut' | 'bulk' | 'maintain',
      })
      setResult(data)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '分析失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-gray-800">AI视觉营养分析</h2>
        <p className="text-sm text-muted-foreground mt-1">
          拍照识别餐食营养，提供减脂/增肌红绿灯评估
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="weight">体重 (kg，可选)</Label>
          <Input
            id="weight"
            type="number"
            placeholder="如 70"
            value={weightKg}
            onChange={(e) => setWeightKg(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>健身目标</Label>
          <Select value={goal} onValueChange={setGoal}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cut">减脂</SelectItem>
              <SelectItem value="bulk">增肌</SelectItem>
              <SelectItem value="maintain">维持</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <ImageUploadZone
        loading={loading}
        preview={preview}
        previewAlt="餐食预览"
        hint="上传餐食照片，支持点击或拖拽，JPG/PNG 最大 5MB"
        onFile={handleFile}
      />

      {loading && (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full rounded-xl" />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        </div>
      )}

      {!loading && !result && !preview && (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 py-10 px-4 text-center">
          <Salad className="size-10 text-muted-foreground/50 mb-3" />
          <p className="text-sm font-medium text-foreground">上传餐食照片开始分析</p>
          <p className="text-xs text-muted-foreground mt-1 max-w-sm">
            可选填体重与健身目标，获得更精准的红绿灯营养评估
          </p>
        </div>
      )}

      {result && !loading && (
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{result.meal_summary}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <MetricCard label="热量" value={result.totals.calories} unit="kcal" />
                <MetricCard label="蛋白质" value={result.totals.protein} unit="g" />
                <MetricCard label="脂肪" value={result.totals.fat} unit="g" />
                <MetricCard label="碳水" value={result.totals.carbs} unit="g" />
              </div>

              <div className="rounded-lg border bg-muted/10 p-3 space-y-2">
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  营养评估
                </p>
                <div className="flex flex-wrap gap-x-6 gap-y-2">
                  <TrafficLight status={result.assessment.lights.calories} label="热量评估" />
                  <TrafficLight status={result.assessment.lights.protein} label="蛋白质评估" />
                  <TrafficLight
                    status={result.assessment.overall}
                    label={`综合: ${
                      result.assessment.overall === 'green'
                        ? '适合'
                        : result.assessment.overall === 'yellow'
                          ? '注意'
                          : '超标'
                    }`}
                  />
                </div>
              </div>

              {result.assessment.tips.length > 0 && (
                <ul className="text-sm text-muted-foreground list-disc pl-5 space-y-1">
                  {result.assessment.tips.map((tip) => (
                    <li key={tip}>{tip}</li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              食材明细
            </p>
            <div className="grid gap-2">
              {result.items.map((item) => (
                <Card key={`${item.name}-${item.grams}`}>
                  <CardContent className="py-3 text-sm flex flex-col sm:flex-row sm:justify-between gap-1">
                    <span className="font-medium">
                      {item.name} ({item.grams}g)
                    </span>
                    <span className="text-muted-foreground">
                      {item.calories} kcal · 蛋白 {item.protein}g
                    </span>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
