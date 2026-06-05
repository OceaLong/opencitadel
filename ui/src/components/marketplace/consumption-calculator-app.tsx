'use client'

import { useRef, useState } from 'react'
import { Camera, Loader2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { marketplaceApi } from '@/lib/api/marketplace'
import { fileApi } from '@/lib/api/file'
import type { ConsumptionAnalysisData } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'

const MAX_SIZE = 5 * 1024 * 1024
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/png']

export function ConsumptionCalculatorApp() {
  const fileRef = useRef<HTMLInputElement>(null)
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
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold text-gray-700">实物消耗计算器</h2>
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
          上传包装图
        </Button>
        <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={loading}>
          <Camera className="size-4" />
          拍照识别
        </Button>
        {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      </div>

      {preview && (
        <img src={preview} alt="包装预览" className="max-h-48 rounded-lg border object-cover" />
      )}

      {result && !result.recognized && (
        <Card>
          <CardContent className="py-4 space-y-3">
            <p className="text-sm text-muted-foreground">{result.message}</p>
            {result.ocr_text && (
              <p className="text-xs text-muted-foreground">识别文本: {result.ocr_text}</p>
            )}
            <div className="flex gap-2 items-end">
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
              <Button onClick={handleManualCalculate} disabled={loading}>
                重新计算
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {result?.recognized && (
        <Card>
          <CardContent className="py-4 space-y-2">
            <p className="text-base font-medium text-gray-700">{result.message}</p>
            {result.ocr_text && (
              <p className="text-xs text-muted-foreground">识别: {result.ocr_text}</p>
            )}
            <div className="text-sm text-muted-foreground">
              总量 {result.total_grams}g · 每次 {result.serving_grams}g · 约 {result.servings} 次
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
