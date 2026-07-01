"use client";

import { useState } from "react";
import { MessageCircle, Salad, Send } from "lucide-react";
import { toast } from "sonner";

import { ImageUploadZone } from "@/components/marketplace/image-upload-zone";
import { TrafficLight } from "@/components/marketplace/traffic-light";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

import { fileApi } from "@/lib/api/file";
import { marketplaceApi } from "@/lib/api/marketplace";
import type { NutritionAnalysisData } from "@/lib/api/types";
import { useRequireAuth } from "@/hooks/use-require-auth";

const MAX_SIZE = 5 * 1024 * 1024;
const ALLOWED_TYPES = ["image/jpeg", "image/jpg", "image/png"];

function MetricCard({ label, value, unit }: { label: string; value: number; unit: string }) {
  return (
    <div className="border-border/70 bg-muted/20 rounded-xl border px-3 py-2.5 text-center">
      <p className="text-muted-foreground text-[11px]">{label}</p>
      <p className="text-foreground mt-0.5 text-base font-semibold">
        {value}
        <span className="text-muted-foreground ml-0.5 text-xs font-normal">{unit}</span>
      </p>
    </div>
  );
}

export function NutritionAnalysisApp({
  initialGoal = "maintain",
}: {
  initialGoal?: "cut" | "bulk" | "maintain";
}) {
  const { requireAuth } = useRequireAuth();
  const [preview, setPreview] = useState<string | null>(null);
  const [weightKg, setWeightKg] = useState("");
  const [goal, setGoal] = useState<string>(initialGoal);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NutritionAnalysisData | null>(null);
  const [followupQuestion, setFollowupQuestion] = useState("");
  const [followupAnswer, setFollowupAnswer] = useState("");
  const [followupLoading, setFollowupLoading] = useState(false);

  const handleFile = async (file: File) => {
    if (!requireAuth("登录后即可使用 AI 营养分析")) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      toast.error("仅支持 JPG/PNG 图片");
      return;
    }
    if (file.size > MAX_SIZE) {
      toast.error("图片不能超过 5MB");
      return;
    }

    setPreview(URL.createObjectURL(file));
    setLoading(true);
    setResult(null);

    try {
      const uploaded = await fileApi.uploadFile({ file });
      const data = await marketplaceApi.analyzeNutrition({
        file_id: uploaded.id,
        weight_kg: weightKg ? Number(weightKg) : undefined,
        goal: goal as "cut" | "bulk" | "maintain",
      });
      setResult(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "分析失败");
    } finally {
      setLoading(false);
    }
  };

  const handleFollowup = async () => {
    if (!result || !followupQuestion.trim()) {
      toast.error("请输入想追问的问题");
      return;
    }
    if (!requireAuth("登录后即可使用 AI 营养分析")) return;
    setFollowupLoading(true);
    setFollowupAnswer("");
    try {
      const data = await marketplaceApi.answerNutritionFollowup({
        analysis: result,
        question: followupQuestion.trim(),
      });
      setFollowupAnswer(data.answer);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "追问失败");
    } finally {
      setFollowupLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">AI视觉营养分析</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          拍照识别餐食营养，提供减脂/增肌红绿灯评估
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        </div>
      )}

      {!loading && !result && !preview && (
        <div className="bg-muted/20 flex flex-col items-center justify-center rounded-xl border border-dashed px-4 py-10 text-center">
          <Salad className="text-muted-foreground/50 mb-3 size-10" />
          <p className="text-foreground text-sm font-medium">上传餐食照片开始分析</p>
          <p className="text-muted-foreground mt-1 max-w-sm text-xs">
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
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <MetricCard label="热量" value={result.totals.calories} unit="kcal" />
                <MetricCard label="蛋白质" value={result.totals.protein} unit="g" />
                <MetricCard label="脂肪" value={result.totals.fat} unit="g" />
                <MetricCard label="碳水" value={result.totals.carbs} unit="g" />
              </div>

              <div className="border-border/70 bg-muted/10 space-y-2 rounded-xl border p-3">
                <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                  营养评估
                </p>
                <div className="flex flex-wrap gap-x-6 gap-y-2">
                  <TrafficLight status={result.assessment.lights.calories} label="热量评估" />
                  <TrafficLight status={result.assessment.lights.protein} label="蛋白质评估" />
                  <TrafficLight
                    status={result.assessment.overall}
                    label={`综合: ${
                      result.assessment.overall === "green"
                        ? "适合"
                        : result.assessment.overall === "yellow"
                          ? "注意"
                          : "超标"
                    }`}
                  />
                </div>
              </div>

              {result.assessment.tips.length > 0 && (
                <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-sm">
                  {result.assessment.tips.map((tip) => (
                    <li key={tip}>{tip}</li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <div className="space-y-2">
            <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
              食材明细
            </p>
            <div className="grid gap-2">
              {result.items.map((item) => (
                <Card key={`${item.name}-${item.grams}`}>
                  <CardContent className="flex flex-col gap-1 py-3 text-sm sm:flex-row sm:justify-between">
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

          <Card className="border-primary/20 bg-primary/5">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <MessageCircle className="size-4" />
                追问营养建议
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                value={followupQuestion}
                onChange={(e) => setFollowupQuestion(e.target.value)}
                placeholder="例如：这顿饭减脂期要怎么调整？蛋白质够吗？"
                className="min-h-20 bg-background/70"
              />
              <Button onClick={handleFollowup} disabled={followupLoading}>
                {followupLoading ? (
                  <Skeleton className="size-4 rounded-full" />
                ) : (
                  <Send className="size-4" />
                )}
                生成建议
              </Button>
              {followupAnswer && (
                <div className="border-border/70 bg-background/80 text-foreground rounded-xl border p-3 text-sm leading-relaxed whitespace-pre-wrap">
                  {followupAnswer}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
