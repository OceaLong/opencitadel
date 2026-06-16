"use client";

import { useEffect, useState } from "react";

import { playReactionSound, vibrate } from "@/components/room/room-sounds";
import { Button } from "@/components/ui/button";
import { bounceIn, motion, reducedVariants } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

const REACTIONS = ["👍", "😂", "🔥", "❤️", "🎉", "😱", "👏", "💯"] as const;

export type FloatingReaction = {
  id: string;
  emoji: string;
  name?: string;
};

type ReactionBarProps = {
  disabled?: boolean;
  onSend: (emoji: string) => void;
  floating: FloatingReaction[];
  onDismiss: (id: string) => void;
  className?: string;
};

export function ReactionBar({
  disabled,
  onSend,
  floating,
  onDismiss,
  className,
}: ReactionBarProps) {
  const reduced = usePrefersReducedMotion();

  const handleSend = (emoji: string) => {
    playReactionSound();
    vibrate(20);
    onSend(emoji);
  };

  return (
    <div className={cn("relative", className)}>
      <div className="pointer-events-none absolute inset-x-0 -top-24 z-10 flex justify-center">
        {floating.map((item) => (
          <FloatingEmoji
            key={item.id}
            emoji={item.emoji}
            name={item.name}
            reduced={reduced}
            onDone={() => onDismiss(item.id)}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {REACTIONS.map((emoji) => (
          <Button
            key={emoji}
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            className="h-8 px-2 text-base"
            onClick={() => handleSend(emoji)}
          >
            {emoji}
          </Button>
        ))}
      </div>
    </div>
  );
}

function FloatingEmoji({
  emoji,
  name,
  reduced,
  onDone,
}: {
  emoji: string;
  name?: string;
  reduced: boolean;
  onDone: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onDone, reduced ? 800 : 1800);
    return () => clearTimeout(t);
  }, [onDone, reduced]);

  return (
    <motion.span
      initial="hidden"
      animate="visible"
      variants={reducedVariants(bounceIn, reduced)}
      className="absolute text-3xl"
      style={{ left: `${20 + Math.random() * 60}%` }}
    >
      <motion.span
        animate={reduced ? {} : { y: -80, opacity: 0 }}
        transition={{ duration: 1.6, ease: "easeOut" }}
        className="inline-flex flex-col items-center"
      >
        <span>{emoji}</span>
        {name ? <span className="text-muted-foreground text-[10px]">{name}</span> : null}
      </motion.span>
    </motion.span>
  );
}
