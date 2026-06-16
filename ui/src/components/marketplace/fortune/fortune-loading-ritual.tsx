"use client";

import type { FortuneMode } from "@/lib/api/types";
import { motion, reducedVariants, scaleIn } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

const MODE_RITUAL: Record<FortuneMode, { emoji: string; text: string; className: string }> = {
  fortune: { emoji: "🌙", text: "星轨流转，运势渐明…", className: "from-rose-500/20 to-violet-500/10" },
  lottery: { emoji: "🎋", text: "签筒轻摇，机缘将至…", className: "from-amber-500/20 to-orange-500/10" },
  divination: { emoji: "☯️", text: "罗盘旋转，卦象浮现…", className: "from-emerald-500/20 to-teal-500/10" },
  astrology: { emoji: "✨", text: "星盘展开，命运低语…", className: "from-indigo-500/20 to-violet-500/10" },
};

type FortuneLoadingRitualProps = {
  mode: FortuneMode;
  streamText?: string;
};

export function FortuneLoadingRitual({ mode, streamText }: FortuneLoadingRitualProps) {
  const reduced = usePrefersReducedMotion();
  const ritual = MODE_RITUAL[mode];

  return (
    <div
      className={cn(
        "flex min-h-[280px] flex-col items-center justify-center rounded-2xl bg-gradient-to-br p-8 text-center",
        ritual.className,
      )}
    >
      <motion.div
        animate={reduced ? {} : { rotate: [0, 8, -8, 0], scale: [1, 1.05, 1] }}
        transition={{ repeat: Infinity, duration: 1.8, ease: "easeInOut" }}
        className="text-6xl"
      >
        {ritual.emoji}
      </motion.div>
      <p className="text-foreground mt-4 text-sm font-medium">{ritual.text}</p>
      {streamText ? (
        <motion.pre
          initial="hidden"
          animate="visible"
          variants={reducedVariants(scaleIn, reduced)}
          className="text-muted-foreground mt-4 max-h-32 w-full overflow-auto whitespace-pre-wrap text-left text-xs leading-relaxed"
        >
          {streamText.slice(-600)}
        </motion.pre>
      ) : (
        <div className="mt-4 flex gap-1">
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="bg-primary/60 size-2 rounded-full"
              animate={reduced ? {} : { opacity: [0.3, 1, 0.3], y: [0, -4, 0] }}
              transition={{ repeat: Infinity, duration: 1, delay: i * 0.15 }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
