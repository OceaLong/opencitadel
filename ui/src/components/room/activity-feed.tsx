"use client";

import { Card, CardContent } from "@/components/ui/card";
import type { RoomEvent } from "@/lib/api/types";
import { fadeInUp, motion, reducedVariants, staggerContainer } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";

function formatEvent(ev: RoomEvent): string {
  switch (ev.type) {
    case "join":
      return `${ev.payload.name ?? "有人"} 加入了房间`;
    case "dice":
      return `${ev.payload.participant_name ?? "有人"} 摇了 ${(ev.payload.results as number[])?.join("+")}`;
    case "tod_draw":
      return `${ev.payload.participant_name ?? "有人"} 抽到${ev.payload.category === "truth" ? "真心话" : "大冒险"}`;
    case "turn":
      return `轮到 ${ev.payload.current_turn_name ?? "下一位"}`;
    case "prompt_add":
      return "房主添加了自定义题目";
    case "reaction":
      return `${ev.payload.participant_name ?? "有人"} 发送 ${ev.payload.emoji ?? "表情"}`;
    default:
      return "房间有新动态";
  }
}

type ActivityFeedProps = {
  feed: RoomEvent[];
};

export function ActivityFeed({ feed }: ActivityFeedProps) {
  const reduced = usePrefersReducedMotion();
  const items = feed.slice().reverse();

  return (
    <Card>
      <CardContent className="space-y-2 pt-5">
        <h3 className="text-foreground text-sm font-semibold">动态</h3>
        <motion.ul
          initial="hidden"
          animate="visible"
          variants={reducedVariants(staggerContainer(0.04), reduced)}
          className="max-h-64 space-y-1 overflow-auto text-xs"
        >
          {items.length === 0 ? (
            <li className="text-muted-foreground py-4 text-center">暂无动态</li>
          ) : (
            items.map((ev) => (
              <motion.li
                key={ev.id}
                variants={reducedVariants(fadeInUp, reduced)}
                className="text-muted-foreground border-border/60 border-b border-dashed py-1.5 last:border-0"
              >
                {formatEvent(ev)}
              </motion.li>
            ))
          )}
        </motion.ul>
      </CardContent>
    </Card>
  );
}
