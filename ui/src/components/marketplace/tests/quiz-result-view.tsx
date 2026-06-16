"use client";

import { Copy, Download, Share2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { fadeInUp, motion, reducedVariants, staggerContainer } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { downloadQuizPoster } from "./quiz-poster";
import type { QuizBank, QuizResult } from "./types";

type QuizResultViewProps = {
  bank: QuizBank;
  result: QuizResult;
  shareUrl?: string;
  onBack?: () => void;
  showActions?: boolean;
};

export function QuizResultView({
  bank,
  result,
  shareUrl = "",
  onBack,
  showActions = true,
}: QuizResultViewProps) {
  const reduced = usePrefersReducedMotion();

  const handleCopy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      toast.success("分享链接已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  const handleDownload = async () => {
    try {
      await downloadQuizPoster(bank, result);
      toast.success("画像海报已下载");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "海报生成失败");
    }
  };

  const breakdown = Object.entries(result.scoreBreakdown ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={reducedVariants(staggerContainer(0.08), reduced)}
      className="mx-auto max-w-lg space-y-6 py-4"
    >
      <Card className="overflow-hidden">
        <div className="from-primary/20 via-violet-500/10 bg-gradient-to-br to-transparent px-6 py-8 text-center">
          <motion.span variants={reducedVariants(fadeInUp, reduced)} className="text-6xl">
            {result.avatar ?? bank.icon}
          </motion.span>
          <p className="text-muted-foreground mt-2 text-xs">{bank.name}</p>
          <motion.h2
            variants={reducedVariants(fadeInUp, reduced)}
            className="text-foreground mt-1 text-2xl font-bold"
          >
            {result.title}
          </motion.h2>
          <p className="text-primary mt-1 text-sm font-medium">{result.code}</p>
          {result.confidence != null ? (
            <p className="text-muted-foreground mt-2 text-xs">匹配度 {result.confidence}%</p>
          ) : null}
        </div>
        <CardContent className="space-y-5 pt-6">
          <motion.p
            variants={reducedVariants(fadeInUp, reduced)}
            className="text-muted-foreground text-sm leading-relaxed"
          >
            {result.summary ?? result.description}
          </motion.p>

          <motion.div variants={reducedVariants(fadeInUp, reduced)} className="flex flex-wrap gap-2">
            {result.traits.map((t) => (
              <span
                key={t}
                className="bg-primary/10 text-primary rounded-full px-3 py-1 text-xs font-medium"
              >
                {t}
              </span>
            ))}
          </motion.div>

          {breakdown.length > 0 ? (
            <motion.div variants={reducedVariants(fadeInUp, reduced)} className="space-y-3">
              <h3 className="text-foreground text-sm font-semibold">维度画像</h3>
              {breakdown.slice(0, 6).map(([dim, pct]) => (
                <div key={dim} className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-muted-foreground">{dim}</span>
                    <span className="text-foreground font-medium">{pct}%</span>
                  </div>
                  <Progress value={pct} className="h-1.5" />
                </div>
              ))}
            </motion.div>
          ) : null}

          {result.strengths?.length ? (
            <ReportSection title="核心优势" items={result.strengths} tone="primary" reduced={reduced} />
          ) : null}
          {result.watchOuts?.length ? (
            <ReportSection title="潜在盲点" items={result.watchOuts} tone="muted" reduced={reduced} />
          ) : null}
          {result.socialStyle ? (
            <motion.div variants={reducedVariants(fadeInUp, reduced)} className="space-y-1">
              <h3 className="text-foreground text-sm font-semibold">社交风格</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">{result.socialStyle}</p>
            </motion.div>
          ) : null}
          {result.growthTips?.length ? (
            <ReportSection title="成长建议" items={result.growthTips} tone="muted" reduced={reduced} />
          ) : null}
          {result.closeTypes?.length ? (
            <motion.div variants={reducedVariants(fadeInUp, reduced)} className="space-y-2">
              <h3 className="text-foreground text-sm font-semibold">相近类型</h3>
              <div className="flex flex-wrap gap-2">
                {result.closeTypes.map((item) => (
                  <span
                    key={item.code}
                    className="bg-muted text-muted-foreground rounded-full px-3 py-1 text-xs"
                  >
                    {item.title} · {item.score}%
                  </span>
                ))}
              </div>
            </motion.div>
          ) : null}
        </CardContent>
      </Card>

      {showActions ? (
        <motion.div variants={reducedVariants(fadeInUp, reduced)} className="flex flex-wrap gap-2">
          {shareUrl ? (
            <>
              <Button variant="outline" onClick={handleCopy}>
                <Copy className="mr-1 size-4" />
                复制分享链接
              </Button>
              <Button variant="outline" onClick={() => window.open(shareUrl, "_blank")}>
                <Share2 className="mr-1 size-4" />
                打开分享页
              </Button>
            </>
          ) : null}
          <Button variant="outline" onClick={() => void handleDownload()}>
            <Download className="mr-1 size-4" />
            下载画像海报
          </Button>
          {onBack ? (
            <Button variant="ghost" onClick={onBack}>
              返回测试列表
            </Button>
          ) : null}
        </motion.div>
      ) : null}
    </motion.div>
  );
}

function ReportSection({
  title,
  items,
  tone,
  reduced,
}: {
  title: string;
  items: string[];
  tone: "primary" | "muted";
  reduced: boolean;
}) {
  return (
    <motion.div variants={reducedVariants(fadeInUp, reduced)} className="space-y-2">
      <h3 className="text-foreground text-sm font-semibold">{title}</h3>
      <ul className="space-y-1">
        {items.map((item) => (
          <li
            key={item}
            className={cn(
              "text-sm leading-relaxed",
              tone === "primary" ? "text-foreground" : "text-muted-foreground",
            )}
          >
            · {item}
          </li>
        ))}
      </ul>
    </motion.div>
  );
}
