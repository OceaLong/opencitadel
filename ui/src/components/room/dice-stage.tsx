"use client";

import { Dices, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { bounceIn, motion, reducedVariants, staggerContainer } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

type DiceStageProps = {
  diceCount: string;
  diceFaces: string;
  onDiceCountChange: (value: string) => void;
  onDiceFacesChange: (value: string) => void;
  rolling: boolean;
  lastDice: number[] | null;
  onRoll: () => void;
  disabled?: boolean;
};

export function DiceStage({
  diceCount,
  diceFaces,
  onDiceCountChange,
  onDiceFacesChange,
  rolling,
  lastDice,
  onRoll,
  disabled,
}: DiceStageProps) {
  const reduced = usePrefersReducedMotion();
  const total = lastDice?.reduce((a, b) => a + b, 0) ?? 0;

  return (
    <Card className="overflow-hidden border-primary/10 shadow-sm">
      <CardContent className="space-y-4 pt-5">
        <div className="flex items-center gap-2">
          <Dices className="text-primary size-5" />
          <h2 className="text-foreground text-sm font-semibold">摇骰子</h2>
        </div>
        <div className="flex gap-3">
          <div className="flex-1">
            <Label className="text-xs">个数</Label>
            <Select value={diceCount} onValueChange={onDiceCountChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["1", "2", "3", "4", "5", "6"].map((n) => (
                  <SelectItem key={n} value={n}>
                    {n} 个
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex-1">
            <Label className="text-xs">面数</Label>
            <Select value={diceFaces} onValueChange={onDiceFacesChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["6", "8", "10", "12", "20"].map((n) => (
                  <SelectItem key={n} value={n}>
                    D{n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <Button className="w-full" size="lg" onClick={onRoll} disabled={rolling || disabled}>
          {rolling ? <Loader2 className="size-4 animate-spin" /> : "摇！"}
        </Button>
        <div
          className={cn(
            "from-primary/10 via-violet-500/5 min-h-[120px] rounded-2xl bg-gradient-to-br to-transparent p-4",
            rolling && "animate-pulse",
          )}
        >
          {lastDice && lastDice.length > 0 ? (
            <motion.div
              initial="hidden"
              animate="visible"
              variants={reducedVariants(staggerContainer(0.06), reduced)}
              className="flex flex-wrap justify-center gap-3"
            >
              {lastDice.map((v, i) => (
                <motion.div
                  key={`${i}-${v}`}
                  variants={reducedVariants(bounceIn, reduced)}
                  className="bg-primary text-primary-foreground flex size-16 items-center justify-center rounded-2xl text-3xl font-bold shadow-lg ring-4 ring-primary/20"
                >
                  {v}
                </motion.div>
              ))}
              <p className="text-foreground w-full text-center text-sm font-medium">
                合计 <span className="text-primary text-2xl font-bold">{total}</span>
              </p>
            </motion.div>
          ) : (
            <p className="text-muted-foreground flex h-full min-h-[88px] items-center justify-center text-sm">
              点击摇骰，结果会在这里揭晓
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
