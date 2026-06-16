"use client";

import { MessageCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fadeInUp, motion, reducedVariants, scaleIn } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

type TodStageProps = {
  lastTod: { category: string; text: string } | null;
  drawing?: boolean;
  onDraw: (category?: "truth" | "dare") => void;
  disabled?: boolean;
  isHost?: boolean;
  customPrompt: string;
  customCategory: "truth" | "dare";
  onCustomPromptChange: (value: string) => void;
  onCustomCategoryChange: (value: "truth" | "dare") => void;
  onAddPrompt: () => void;
};

export function TodStage({
  lastTod,
  drawing,
  onDraw,
  disabled,
  isHost,
  customPrompt,
  customCategory,
  onCustomPromptChange,
  onCustomCategoryChange,
  onAddPrompt,
}: TodStageProps) {
  const reduced = usePrefersReducedMotion();

  return (
    <Card className="overflow-hidden border-rose-500/10 shadow-sm">
      <CardContent className="space-y-4 pt-5">
        <div className="flex items-center gap-2">
          <MessageCircle className="size-5 text-rose-500" />
          <h2 className="text-foreground text-sm font-semibold">真心话大冒险</h2>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Button
            variant="outline"
            className="border-rose-500/30 hover:bg-rose-500/5"
            onClick={() => onDraw("truth")}
            disabled={drawing || disabled}
          >
            真心话
          </Button>
          <Button
            variant="outline"
            className="border-violet-500/30 hover:bg-violet-500/5"
            onClick={() => onDraw("dare")}
            disabled={drawing || disabled}
          >
            大冒险
          </Button>
          <Button onClick={() => onDraw()} disabled={drawing || disabled}>
            随机
          </Button>
        </div>
        <div className="relative min-h-[120px]">
          {drawing ? (
            <div className="flex h-full min-h-[120px] items-center justify-center">
              <motion.div
                animate={reduced ? {} : { rotate: [0, -8, 8, 0] }}
                transition={{ repeat: Infinity, duration: 0.6 }}
                className="bg-gradient-to-br from-rose-500/20 to-violet-500/20 flex size-24 items-center justify-center rounded-2xl text-3xl shadow-inner"
              >
                🎴
              </motion.div>
            </div>
          ) : lastTod ? (
            <motion.div
              key={lastTod.text}
              initial="hidden"
              animate="visible"
              variants={reducedVariants(scaleIn, reduced)}
              className={cn(
                "rounded-2xl border p-5 text-center shadow-sm",
                lastTod.category === "truth"
                  ? "border-rose-500/20 bg-gradient-to-br from-rose-500/10 to-transparent"
                  : "border-violet-500/20 bg-gradient-to-br from-violet-500/10 to-transparent",
              )}
            >
              <span
                className={cn(
                  "text-xs font-semibold uppercase tracking-widest",
                  lastTod.category === "truth" ? "text-rose-500" : "text-violet-500",
                )}
              >
                {lastTod.category === "truth" ? "真心话" : "大冒险"}
              </span>
              <p className="text-foreground mt-3 text-base leading-relaxed font-medium">
                {lastTod.text}
              </p>
            </motion.div>
          ) : (
            <p className="text-muted-foreground flex min-h-[120px] items-center justify-center text-sm">
              抽一张卡牌，看看命运安排什么
            </p>
          )}
        </div>
        {isHost ? (
          <motion.div
            initial="hidden"
            animate="visible"
            variants={reducedVariants(fadeInUp, reduced)}
            className="space-y-2 border-t pt-3"
          >
            <Label className="text-xs">房主：添加自定义题目</Label>
            <Select value={customCategory} onValueChange={(v) => onCustomCategoryChange(v as "truth" | "dare")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="truth">真心话</SelectItem>
                <SelectItem value="dare">大冒险</SelectItem>
              </SelectContent>
            </Select>
            <Input
              value={customPrompt}
              onChange={(e) => onCustomPromptChange(e.target.value)}
              placeholder="输入自定义题目"
            />
            <Button variant="outline" size="sm" onClick={onAddPrompt}>
              添加
            </Button>
          </motion.div>
        ) : null}
      </CardContent>
    </Card>
  );
}
