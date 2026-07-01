"use client";

import { useEffect, useRef, useState } from "react";
import { Calculator, MessageSquareText, Package } from "lucide-react";
import { toast } from "sonner";

import { ImageUploadZone } from "@/components/marketplace/image-upload-zone";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

import { fileApi } from "@/lib/api/file";
import { marketplaceApi } from "@/lib/api/marketplace";
import type { ConsumptionAnalysisData } from "@/lib/api/types";
import { useRequireAuth } from "@/hooks/use-require-auth";

const MAX_SIZE = 5 * 1024 * 1024;
const ALLOWED_TYPES = ["image/jpeg", "image/jpg", "image/png"];

export function ConsumptionCalculatorApp({
  initialTotalGrams,
  initialServingGrams,
}: {
  initialTotalGrams?: number;
  initialServingGrams?: number;
}) {
  const { requireAuth } = useRequireAuth();
  const [preview, setPreview] = useState<string | null>(null);
  const [servingGrams, setServingGrams] = useState(String(initialServingGrams ?? 50));
  const [manualTotal, setManualTotal] = useState(initialTotalGrams ? String(initialTotalGrams) : "");
  const [correction, setCorrection] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ConsumptionAnalysisData | null>(null);
  const autoCalculatedRef = useRef(false);

  const handleFile = async (file: File) => {
    if (!requireAuth("登录后即可使用 AI 消耗计算")) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      toast.error("仅支持 JPG/PNG 图片");
      return;
    }
    if (file.size > MAX_SIZE) {
      toast.error("图片不能超过 5MB");
      return;
    }
    const serving = Number(servingGrams);
    if (!serving || serving <= 0) {
      toast.error("请输入有效的单次食用量");
      return;
    }

    setPreview(URL.createObjectURL(file));
    setLoading(true);
    setResult(null);

    try {
      const uploaded = await fileApi.uploadFile({ file });
      const data = await marketplaceApi.analyzeConsumption({
        file_id: uploaded.id,
        serving_grams: serving,
      });
      setResult(data);
      if (!data.recognized) {
        toast.message("未能自动识别，可手动输入总量后计算");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "识别失败");
    } finally {
      setLoading(false);
    }
  };

  const handleManualCalculate = async () => {
    const total = Number(manualTotal);
    const serving = Number(servingGrams);
    if (!total || total <= 0 || !serving || serving <= 0) {
      toast.error("请输入有效的总量和单次食用量");
      return;
    }
    if (!requireAuth("登录后即可使用 AI 消耗计算")) return;
    setLoading(true);
    try {
      const data = await marketplaceApi.calculateConsumption({
        total_grams: total,
        serving_grams: serving,
      });
      setResult(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "计算失败");
    } finally {
      setLoading(false);
    }
  };

  const handleCorrection = async () => {
    const serving = Number(servingGrams);
    if (!correction.trim() || !serving || serving <= 0) {
      toast.error("请输入修正说明和有效单次食用量");
      return;
    }
    if (!requireAuth("登录后即可使用 AI 消耗计算")) return;
    setLoading(true);
    try {
      const data = await marketplaceApi.correctConsumption({
        text: correction.trim(),
        serving_grams: serving,
      });
      setResult(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "修正失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!initialTotalGrams || autoCalculatedRef.current) return;
    autoCalculatedRef.current = true;
    void handleManualCalculate();
    // handleManualCalculate intentionally uses initialized fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTotalGrams]);

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">实物消耗计算器</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          拍摄包装净含量标识，结合单次用量计算可食用次数
        </p>
      </div>

      <div className="max-w-xs space-y-2">
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
        <div className="bg-muted/20 flex flex-col items-center justify-center rounded-xl border border-dashed px-4 py-10 text-center">
          <Package className="text-muted-foreground/50 mb-3 size-10" />
          <p className="text-foreground text-sm font-medium">上传包装照片开始识别</p>
          <p className="text-muted-foreground mt-1 max-w-sm text-xs">
            先填写单次食用量，系统将自动识别净含量并计算可食用次数
          </p>
        </div>
      )}

      {result && !result.recognized && !loading && (
        <Card className="border-amber-200 bg-amber-50/30">
          <CardContent className="space-y-3 py-4">
            <p className="text-foreground text-sm">{result.message}</p>
            {result.ocr_text && (
              <p className="text-muted-foreground bg-background/60 rounded-md px-2 py-1.5 text-xs">
                识别文本: {result.ocr_text}
              </p>
            )}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="flex-1 space-y-2">
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
          <CardContent className="space-y-4 py-5">
            <div className="flex items-start gap-3">
              <div className="bg-primary/10 flex size-12 shrink-0 items-center justify-center rounded-full">
                <Calculator className="text-primary size-6" />
              </div>
              <div className="min-w-0">
                <p className="text-muted-foreground text-sm">{result.message}</p>
                <p className="text-foreground mt-1 text-3xl font-bold">
                  约 {result.servings}
                  <span className="text-muted-foreground ml-1 text-base font-normal">次</span>
                </p>
                <p className="text-muted-foreground mt-1 text-xs">可食用次数</p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3">
              <div className="border-border/70 bg-background/80 rounded-xl border px-3 py-2">
                <p className="text-muted-foreground text-[11px]">总量</p>
                <p className="font-medium">{result.total_grams} g</p>
              </div>
              <div className="border-border/70 bg-background/80 rounded-xl border px-3 py-2">
                <p className="text-muted-foreground text-[11px]">每次</p>
                <p className="font-medium">{result.serving_grams} g</p>
              </div>
              <div className="border-border/70 bg-background/80 rounded-xl border px-3 py-2">
                <p className="text-muted-foreground text-[11px]">可食用</p>
                <p className="text-primary font-medium">{result.servings} 次</p>
              </div>
            </div>

            {result.ocr_text && (
              <p className="text-muted-foreground bg-background/60 rounded-md px-2 py-1.5 text-xs">
                识别: {result.ocr_text}
              </p>
            )}
            <div className="border-border/70 bg-background/70 space-y-2 rounded-xl border p-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                <MessageSquareText className="size-4" />
                自然语言修正
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  value={correction}
                  onChange={(e) => setCorrection(e.target.value)}
                  placeholder="如：其实净含量是 1.2kg"
                />
                <Button onClick={handleCorrection} disabled={loading} className="shrink-0">
                  重新计算
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
