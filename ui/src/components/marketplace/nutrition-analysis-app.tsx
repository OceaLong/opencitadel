'use client'

import { useRef, useState } from 'react'
import { Camera, Loader2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import { fileApi } from '@/lib/api/file'
import type { NutritionAnalysisData } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
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

const MAX_SIZE = 5 * 1024 * 1024
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/png']

export function NutritionAnalysisApp() {
  const fileRef = useRef<HTMLInputElement>(null)
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
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold text-gray-700">AI视觉营养分析</h2>
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

      <div className="flex flex-wrap gap-2">
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) handleFile(file)
          }}
        />
        <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={loading}>
          <Upload className="size-4" />
          上传图片
        </Button>
        <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={loading}>
          <Camera className="size-4" />
          拍照上传
        </Button>
        {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      </div>

      {preview && (
        <img src={preview} alt="餐食预览" className="max-h-48 rounded-lg border object-cover" />
      )}

      {result && (
        <div className="space-y-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{result.meal_summary}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                <div>热量: {result.totals.calories} kcal</div>
                <div>蛋白质: {result.totals.protein} g</div>
                <div>脂肪: {result.totals.fat} g</div>
                <div>碳水: {result.totals.carbs} g</div>
              </div>
              <div className="flex flex-wrap gap-4">
                <TrafficLight status={result.assessment.lights.calories} label="热量评估" />
                <TrafficLight status={result.assessment.lights.protein} label="蛋白质评估" />
                <TrafficLight
                  status={result.assessment.overall}
                  label={`综合: ${result.assessment.overall === 'green' ? '适合' : result.assessment.overall === 'yellow' ? '注意' : '超标'}`}
                />
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

          <div className="grid gap-2">
            {result.items.map((item) => (
              <Card key={`${item.name}-${item.grams}`}>
                <CardContent className="py-3 text-sm flex justify-between gap-2">
                  <span>{item.name} ({item.grams}g)</span>
                  <span className="text-muted-foreground">
                    {item.calories} kcal · 蛋白 {item.protein}g
                  </span>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
