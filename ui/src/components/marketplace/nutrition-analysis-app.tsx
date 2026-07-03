"use client";

import { useState } from "react";
import { MessageCircle, Salad, Send } from "lucide-react";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("marketplaceApps.nutritionAnalysis");
  const tShared = useTranslations("marketplaceApps.shared");
  const tAuth = useTranslations("auth");
  const { requireAuth } = useRequireAuth();
  const [preview, setPreview] = useState<string | null>(null);
  const [weightKg, setWeightKg] = useState("");
  const [goal, setGoal] = useState<string>(initialGoal);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NutritionAnalysisData | null>(null);
  const [followupQuestion, setFollowupQuestion] = useState("");
  const [followupAnswer, setFollowupAnswer] = useState("");
  const [followupLoading, setFollowupLoading] = useState(false);

  const overallStatusLabel = (status: "green" | "yellow" | "red") => {
    if (status === "green") return t("overallGreen");
    if (status === "yellow") return t("overallYellow");
    return t("overallRed");
  };

  const handleFile = async (file: File) => {
    if (!requireAuth(tAuth("loginToNutrition"))) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      toast.error(tShared("jpgPngOnly"));
      return;
    }
    if (file.size > MAX_SIZE) {
      toast.error(tShared("imageTooLarge5mb"));
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
      toast.error(e instanceof Error ? e.message : t("analyzeFailed"));
    } finally {
      setLoading(false);
    }
  };

  const handleFollowup = async () => {
    if (!result || !followupQuestion.trim()) {
      toast.error(t("followupRequired"));
      return;
    }
    if (!requireAuth(tAuth("loginToNutrition"))) return;
    setFollowupLoading(true);
    setFollowupAnswer("");
    try {
      const data = await marketplaceApi.answerNutritionFollowup({
        analysis: result,
        question: followupQuestion.trim(),
      });
      setFollowupAnswer(data.answer);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("followupFailed"));
    } finally {
      setFollowupLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="weight">{t("weightLabel")}</Label>
          <Input
            id="weight"
            type="number"
            placeholder={t("weightPlaceholder")}
            value={weightKg}
            onChange={(e) => setWeightKg(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>{t("goalLabel")}</Label>
          <Select value={goal} onValueChange={setGoal}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cut">{t("goalCut")}</SelectItem>
              <SelectItem value="bulk">{t("goalBulk")}</SelectItem>
              <SelectItem value="maintain">{t("goalMaintain")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <ImageUploadZone
        loading={loading}
        preview={preview}
        previewAlt={t("mealPreviewAlt")}
        hint={t("uploadHint")}
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
          <p className="text-foreground text-sm font-medium">{t("emptyTitle")}</p>
          <p className="text-muted-foreground mt-1 max-w-sm text-xs">{t("emptyHint")}</p>
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
                <MetricCard label={t("metricCalories")} value={result.totals.calories} unit="kcal" />
                <MetricCard label={t("metricProtein")} value={result.totals.protein} unit="g" />
                <MetricCard label={t("metricFat")} value={result.totals.fat} unit="g" />
                <MetricCard label={t("metricCarbs")} value={result.totals.carbs} unit="g" />
              </div>

              <div className="border-border/70 bg-muted/10 space-y-2 rounded-xl border p-3">
                <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                  {t("assessmentTitle")}
                </p>
                <div className="flex flex-wrap gap-x-6 gap-y-2">
                  <TrafficLight status={result.assessment.lights.calories} label={t("caloriesAssessment")} />
                  <TrafficLight status={result.assessment.lights.protein} label={t("proteinAssessment")} />
                  <TrafficLight
                    status={result.assessment.overall}
                    label={t("overallLabel", {
                      status: overallStatusLabel(result.assessment.overall),
                    })}
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
              {t("ingredientsTitle")}
            </p>
            <div className="grid gap-2">
              {result.items.map((item) => (
                <Card key={`${item.name}-${item.grams}`}>
                  <CardContent className="flex flex-col gap-1 py-3 text-sm sm:flex-row sm:justify-between">
                    <span className="font-medium">
                      {item.name} ({item.grams}g)
                    </span>
                    <span className="text-muted-foreground">
                      {t("itemNutrition", {
                        calories: item.calories,
                        protein: item.protein,
                      })}
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
                {t("followupTitle")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                value={followupQuestion}
                onChange={(e) => setFollowupQuestion(e.target.value)}
                placeholder={t("followupPlaceholder")}
                className="min-h-20 bg-background/70"
              />
              <Button onClick={handleFollowup} disabled={followupLoading}>
                {followupLoading ? (
                  <Skeleton className="size-4 rounded-full" />
                ) : (
                  <Send className="size-4" />
                )}
                {t("generateAdvice")}
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
