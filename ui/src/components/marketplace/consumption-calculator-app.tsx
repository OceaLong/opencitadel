'use client'

import { useState } from 'react'
import { Calculator, Package } from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import { fileApi } from '@/lib/api/file'
import type { ConsumptionAnalysisData } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { ImageUploadZone } from '@/components/marketplace/image-upload-zone'
import { Skeleton } from '@/components/ui/skeleton'

const MAX_SIZE = 5 * 1024 * 1024
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/png']

export function ConsumptionCalculatorApp() {
  const [preview, setPreview] = useState<string | null>(null)
  const [servingGrams, setServingGrams] = useState('50')
  const [manualTotal, setManualTotal] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ConsumptionAnalysisData | null>(null)

  const handleFile = async (file: File) => {
    if (!ALLOWED_TYPES.includes(file.type)) {
      toast.error('仅支持 JPG/PNG 图片')
      return
    }
    if (file.size > MAX_SIZE) {
      toast.error('图片不能超过 5MB')
      return
    }
    const serving = Number(servingGrams)
    if (!serving || serving <= 0) {
      toast.error('请输入有效的单次食用量')
      return
    }

    setPreview(URL.createObjectURL(file))
    setLoading(true)
    setResult(null)

    try {
      const uploaded = await fileApi.uploadFile({ file })
      const data = await marketplaceApi.analyzeConsumption({
        file_id: uploaded.id,
        serving_grams: serving,
      })
      setResult(data)
      if (!data.recognized) {
        toast.message('未能自动识别，可手动输入总量后计算')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '识别失败')
    } finally {
      setLoading(false)
    }
  }

  const handleManualCalculate = async () => {
    const total = Number(manualTotal)
    const serving = Number(servingGrams)
    if (!total || total <= 0 || !serving || serving <= 0) {
      toast.error('请输入有效的总量和单次食用量')
      return
    }
    setLoading(true)
    try {
      const data = await marketplaceApi.calculateConsumption({
        total_grams: total,
        serving_grams: serving,
      })
      setResult(data)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '计算失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-gray-800">实物消耗计算器</h2>
        <p className="text-sm text-muted-foreground mt-1">
          拍摄包装净含量标识，结合单次用量计算可食用次数
        </p>
      </div>

      <div className="space-y-2 max-w-xs">
        <Label htmlFor="serving">单次食用量 (g)</Label>
        <Input
          id="serving"
          type="number"
          value={servingGrams}
          onChange={(e) => setServingGrams(e.target.value)}
        />
      </div>

      <ImageUploadZone
        loading={loading}
        preview={preview}
        previewAlt="包装预览"
        hint="上传包装净含量照片，支持点击或拖拽，JPG/PNG 最大 5MB"
        onFile={handleFile}
      />

      {loading && (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-lg" />
        </div>
      )}

      {!loading && !result && !preview && (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 py-10 px-4 text-center">
          <Package className="size-10 text-muted-foreground/50 mb-3" />
          <p className="text-sm font-medium text-foreground">上传包装照片开始识别</p>
          <p className="text-xs text-muted-foreground mt-1 max-w-sm">
            先填写单次食用量，系统将自动识别净含量并计算可食用次数
          </p>
        </div>
      )}

      {result && !result.recognized && !loading && (
        <Card className="border-amber-200 bg-amber-50/30">
          <CardContent className="py-4 space-y-3">
            <p className="text-sm text-foreground">{result.message}</p>
            {result.ocr_text && (
              <p className="text-xs text-muted-foreground rounded-md bg-background/60 px-2 py-1.5">
                识别文本: {result.ocr_text}
              </p>
            )}
            <div className="flex flex-col sm:flex-row gap-2 sm:items-end">
              <div className="space-y-2 flex-1">
                <Label htmlFor="manual-total">手动输入总量 (g)</Label>
                <Input
                  id="manual-total"
                  type="number"
                  placeholder="如 1000"
                  value={manualTotal}
                  onChange={(e) => setManualTotal(e.target.value)}
                />
              </div>
              <Button onClick={handleManualCalculate} disabled={loading} className="shrink-0">
                <Calculator className="size-4" />
                重新计算
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {result?.recognized && !loading && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="py-5 space-y-4">
            <div className="flex items-start gap-3">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-full bg-primary/10">
                <Calculator className="size-6 text-primary" />
              </div>
              <div className="min-w-0">
                <p className="text-sm text-muted-foreground">{result.message}</p>
                <p className="text-3xl font-bold text-foreground mt-1">
                  约 {result.servings}
                  <span className="text-base font-normal text-muted-foreground ml-1">次</span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">可食用次数</p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-sm">
              <div className="rounded-lg border bg-background/80 px-3 py-2">
                <p className="text-[11px] text-muted-foreground">总量</p>
                <p className="font-medium">{result.total_grams} g</p>
              </div>
              <div className="rounded-lg border bg-background/80 px-3 py-2">
                <p className="text-[11px] text-muted-foreground">每次</p>
                <p className="font-medium">{result.serving_grams} g</p>
              </div>
              <div className="rounded-lg border bg-background/80 px-3 py-2">
                <p className="text-[11px] text-muted-foreground">可食用</p>
                <p className="font-medium text-primary">{result.servings} 次</p>
              </div>
            </div>

            {result.ocr_text && (
              <p className="text-xs text-muted-foreground rounded-md bg-background/60 px-2 py-1.5">
                识别: {result.ocr_text}
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
