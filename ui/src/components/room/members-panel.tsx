"use client";

import { Users } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import type { RoomData } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type MembersPanelProps = {
  room: RoomData;
  participantId: string | null;
};

export function MembersPanel({ room, participantId }: MembersPanelProps) {
  const onlineCount = room.participants.filter((p) => p.online).length;

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center gap-2">
          <Users className="text-muted-foreground size-4" />
          <h3 className="text-foreground text-sm font-semibold">
            在线成员 ({onlineCount}/{room.participants.length})
          </h3>
        </div>
        <ul className="space-y-2">
          {room.participants.map((p) => (
            <li
              key={p.id}
              className={cn(
                "flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors",
                p.id === room.current_turn_id && "bg-primary/10 ring-primary/30 ring-1",
                p.id === participantId && "font-medium",
              )}
            >
              <span
                className={cn(
                  "flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                  p.online ? "bg-emerald-500/15 text-emerald-600" : "bg-muted text-muted-foreground",
                )}
              >
                {p.name.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              <span
                className={cn(
                  "size-2 shrink-0 rounded-full",
                  p.online ? "bg-emerald-500" : "bg-muted-foreground/40",
                )}
              />
              {p.id === room.host_participant_id ? (
                <span className="text-muted-foreground shrink-0 text-[10px]">房主</span>
              ) : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
