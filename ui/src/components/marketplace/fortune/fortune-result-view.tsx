"use client";

import { Copy, Download, Share2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import type { FortunePredictionData } from "@/lib/api/types";
import { cn } from "@/lib/utils";

import {
  buildFortuneShareUrl,
  downloadFortunePoster,
  FORTUNE_MODES,
  MODE_LABELS,
  modeAccent,
} from "./fortune-utils";

type FortuneResultViewProps = {
  data: FortunePredictionData;
  onReset?: () => void;
  showReset?: boolean;
};

export function FortuneResultView({ data, onReset, showReset = true }: FortuneResultViewProps) {
  const modeMeta = FORTUNE_MODES.find((m) => m.id === data.result.mode);
  const shareUrl = buildFortuneShareUrl(data.share_id);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      toast.success("分享链接已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  const handleDownload = async () => {
    try {
      await downloadFortunePoster(data);
      toast.success("海报已下载");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "海报生成失败");
    }
  };

  return (
    <div className="mx-auto max-w-lg space-y-6 py-4">
      <Card className="overflow-hidden">
        <div
          className={cn(
            "bg-gradient-to-br to-transparent px-6 py-8 text-center",
            modeAccent(data.result.mode),
          )}
        >
          <span className="text-5xl">{modeMeta?.icon ?? "🔮"}</span>
          <p className="text-muted-foreground mt-2 text-xs">
            {MODE_LABELS[data.result.mode]}
          </p>
          <h2 className="text-foreground mt-1 text-2xl font-bold">{data.result.title}</h2>
          <p className="text-primary mt-2 text-sm leading-relaxed">{data.result.summary}</p>
        </div>
        <CardContent className="space-y-5 pt-6">
          {data.result.sections.map((section) => (
            <div key={section.heading} className="space-y-1">
              <h3 className="text-foreground text-sm font-semibold">{section.heading}</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">{section.content}</p>
            </div>
          ))}

          <div className="bg-muted/40 flex flex-wrap gap-2 rounded-xl p-4">
            <Badge variant="secondary">幸运色 {data.result.lucky_items.color}</Badge>
            <Badge variant="secondary">数字 {data.result.lucky_items.number}</Badge>
            <Badge variant="secondary">关键词 {data.result.lucky_items.keyword}</Badge>
            {data.result.lucky_items.element ? (
              <Badge variant="secondary">元素 {data.result.lucky_items.element}</Badge>
            ) : null}
          </div>

          <p className="text-muted-foreground text-xs">{data.result.disclaimer}</p>
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={handleCopy}>
          <Copy className="mr-1 size-4" />
          复制分享链接
        </Button>
        <Button variant="outline" onClick={() => window.open(shareUrl, "_blank")}>
          <Share2 className="mr-1 size-4" />
          打开分享页
        </Button>
        <Button variant="outline" onClick={() => void handleDownload()}>
          <Download className="mr-1 size-4" />
          下载海报
        </Button>
        {showReset && onReset ? (
          <Button variant="ghost" onClick={onReset}>
            再测一次
          </Button>
        ) : null}
      </div>
    </div>
  );
}
