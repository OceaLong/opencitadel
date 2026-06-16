"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { FortuneLoadingRitual } from "@/components/marketplace/fortune/fortune-loading-ritual";
import { FortuneResultView } from "@/components/marketplace/fortune/fortune-result-view";
import { FORTUNE_MODES } from "@/components/marketplace/fortune/fortune-utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { fadeInUp, motion, reducedVariants } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";

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
  const reduced = usePrefersReducedMotion();
  const [mode, setMode] = useState<FortuneMode>(initialMode);
  const [question, setQuestion] = useState(initialQuestion);
  const [nickname, setNickname] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [birthTime, setBirthTime] = useState("");
  const [birthPlace, setBirthPlace] = useState("");
  const [loading, setLoading] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [result, setResult] = useState<FortunePredictionData | null>(null);

  const predict = async () => {
    if (!question.trim()) {
      toast.error("请输入你想预测的问题");
      return;
    }
    setLoading(true);
    setStreamText("");
    const params = {
      mode,
      question: question.trim(),
      input_profile: {
        nickname: nickname.trim() || undefined,
        birth_date: birthDate.trim() || undefined,
        birth_time: birthTime.trim() || undefined,
        birth_place: birthPlace.trim() || undefined,
      },
    };

    try {
      await marketplaceApi.predictFortuneStream(params, {
        onDelta: (text) => setStreamText((prev) => prev + text),
        onDone: (data) => setResult(data),
        onError: async (message) => {
          try {
            const data = await marketplaceApi.predictFortune(params);
            setResult(data);
          } catch (e) {
            toast.error(e instanceof Error ? e.message : message);
          }
        },
      });
    } catch {
      try {
        const data = await marketplaceApi.predictFortune(params);
        setResult(data);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "预测失败");
      }
    } finally {
      setLoading(false);
      setStreamText("");
    }
  };

  if (loading) {
    return <FortuneLoadingRitual mode={mode} streamText={streamText} />;
  }

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
          <motion.button
            key={item.id}
            type="button"
            onClick={() => setMode(item.id)}
            whileHover={reduced ? undefined : { y: -4, scale: 1.01 }}
            whileTap={reduced ? undefined : { scale: 0.98 }}
            className="text-left"
          >
            <Card
              className={cn(
                "h-full transition-shadow",
                mode === item.id
                  ? "border-primary/50 bg-primary/5 shadow-md ring-2 ring-primary/20"
                  : "hover:border-primary/30 hover:shadow-sm",
              )}
            >
              <CardContent className="space-y-2 pt-5">
                <motion.span
                  animate={mode === item.id && !reduced ? { rotate: [0, -6, 6, 0] } : {}}
                  transition={{ duration: 0.6 }}
                  className="inline-block text-3xl"
                >
                  {item.icon}
                </motion.span>
                <h3 className="text-foreground text-sm font-semibold">{item.name}</h3>
                <p className="text-muted-foreground text-xs">{item.description}</p>
              </CardContent>
            </Card>
          </motion.button>
        ))}
      </div>

      <motion.div initial="hidden" animate="visible" variants={reducedVariants(fadeInUp, reduced)}>
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
      </motion.div>
    </div>
  );
}
