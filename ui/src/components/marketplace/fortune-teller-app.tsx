"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { FortuneResultView } from "@/components/marketplace/fortune/fortune-result-view";
import { FORTUNE_MODES } from "@/components/marketplace/fortune/fortune-utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { marketplaceApi } from "@/lib/api/marketplace";
import type { FortuneMode, FortunePredictionData } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type FortuneTellerAppProps = {
  initialMode?: FortuneMode;
  initialQuestion?: string;
};

export function FortuneTellerApp({
  initialMode = "fortune",
  initialQuestion = "",
}: FortuneTellerAppProps) {
  const [mode, setMode] = useState<FortuneMode>(initialMode);
  const [question, setQuestion] = useState(initialQuestion);
  const [nickname, setNickname] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [birthTime, setBirthTime] = useState("");
  const [birthPlace, setBirthPlace] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<FortunePredictionData | null>(null);

  const predict = async () => {
    if (!question.trim()) {
      toast.error("请输入你想预测的问题");
      return;
    }
    setLoading(true);
    try {
      const data = await marketplaceApi.predictFortune({
        mode,
        question: question.trim(),
        input_profile: {
          nickname: nickname.trim() || undefined,
          birth_date: birthDate.trim() || undefined,
          birth_time: birthTime.trim() || undefined,
          birth_place: birthPlace.trim() || undefined,
        },
      });
      setResult(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "预测失败");
    } finally {
      setLoading(false);
    }
  };

  if (result) {
    return (
      <FortuneResultView
        data={result}
        onReset={() => {
          setResult(null);
        }}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-foreground text-lg font-semibold">AI 运势预测</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          运势预测、抽签、算命、星盘推演，生成可分享的精美结果
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {FORTUNE_MODES.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setMode(item.id)}
            className="text-left"
          >
            <Card
              className={cn(
                "h-full transition-colors",
                mode === item.id
                  ? "border-primary/50 bg-primary/5"
                  : "hover:border-primary/30",
              )}
            >
              <CardContent className="space-y-2 pt-5">
                <span className="text-3xl">{item.icon}</span>
                <h3 className="text-foreground text-sm font-semibold">{item.name}</h3>
                <p className="text-muted-foreground text-xs">{item.description}</p>
              </CardContent>
            </Card>
          </button>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="space-y-2">
            <Label htmlFor="fortune-question">你想问什么？</Label>
            <Textarea
              id="fortune-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="例如：最近事业运势如何？本月感情有什么指引？"
              className="min-h-24"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="fortune-nickname">昵称（可选）</Label>
              <Input
                id="fortune-nickname"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                placeholder="你的称呼"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fortune-birth-date">出生日期（可选）</Label>
              <Input
                id="fortune-birth-date"
                value={birthDate}
                onChange={(e) => setBirthDate(e.target.value)}
                placeholder="如 1995-08-16"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fortune-birth-time">出生时间（可选）</Label>
              <Input
                id="fortune-birth-time"
                value={birthTime}
                onChange={(e) => setBirthTime(e.target.value)}
                placeholder="如 14:30"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fortune-birth-place">出生地点（可选）</Label>
              <Input
                id="fortune-birth-place"
                value={birthPlace}
                onChange={(e) => setBirthPlace(e.target.value)}
                placeholder="如 上海"
              />
            </div>
          </div>

          {mode === "astrology" ? (
            <p className="text-muted-foreground text-xs">
              星盘推演建议填写出生信息，可获得更贴近的解读。
            </p>
          ) : null}

          <Button onClick={() => void predict()} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            开始预测
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
