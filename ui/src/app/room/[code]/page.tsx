"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Copy, Loader2, SkipForward, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { loadParticipant } from "@/components/marketplace/room-app";
import { ActivityFeed } from "@/components/room/activity-feed";
import { ConnectionStatusBadge } from "@/components/room/connection-status";
import { DiceStage } from "@/components/room/dice-stage";
import { InviteCard } from "@/components/room/invite-card";
import { MembersPanel } from "@/components/room/members-panel";
import { playDiceSound, playTodSound, vibrate } from "@/components/room/room-sounds";
import { ReactionBar, type FloatingReaction } from "@/components/room/reaction-bar";
import { TodStage } from "@/components/room/tod-stage";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useRoomSSE } from "@/hooks/use-room-sse";
import { roomApi } from "@/lib/api/room";
import type { RoomData, RoomEvent } from "@/lib/api/types";

export default function RoomPage() {
  const params = useParams();
  const code = (params.code as string).toUpperCase();

  const [room, setRoom] = useState<RoomData | null>(null);
  const [participantId, setParticipantId] = useState<string | null>(null);
  const [participantName, setParticipantName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [rolling, setRolling] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [lastDice, setLastDice] = useState<number[] | null>(null);
  const [lastTod, setLastTod] = useState<{ category: string; text: string } | null>(null);
  const [diceCount, setDiceCount] = useState("1");
  const [diceFaces, setDiceFaces] = useState("6");
  const [customPrompt, setCustomPrompt] = useState("");
  const [customCategory, setCustomCategory] = useState<"truth" | "dare">("truth");
  const [feed, setFeed] = useState<RoomEvent[]>([]);
  const [floatingReactions, setFloatingReactions] = useState<FloatingReaction[]>([]);

  const diceRollbackRef = useRef<number[] | null>(null);
  const todRollbackRef = useRef<{ category: string; text: string } | null>(null);

  const applyRoom = useCallback((data: RoomData) => {
    setRoom(data);
    if (data.recent_events) {
      setFeed(data.recent_events.slice(-20));
    }
  }, []);

  const appendFeed = useCallback((event: RoomEvent) => {
    setFeed((prev) => [...prev.slice(-19), event]);
  }, []);

  const addFloatingReaction = useCallback(
    (payload: { emoji: string; participant_name?: string }) => {
      setFloatingReactions((prev) => [
        ...prev.slice(-6),
        { id: `${Date.now()}-${Math.random()}`, emoji: payload.emoji, name: payload.participant_name },
      ]);
    },
    [],
  );

  const handleTurn = useCallback((payload: Record<string, unknown>) => {
    setRoom((prev) =>
      prev
        ? {
            ...prev,
            current_turn_index: payload.current_turn_index as number,
            current_turn_id: payload.current_turn_id as string,
            current_turn_name: payload.current_turn_name as string,
          }
        : prev,
    );
  }, []);

  const { connectionStatus } = useRoomSSE({
    code,
    participantId,
    participantName,
    onRoom: applyRoom,
    onDice: setLastDice,
    onTod: setLastTod,
    onTurn: handleTurn,
    onEvent: appendFeed,
    onReaction: addFloatingReaction,
  });

  const load = useCallback(async () => {
    try {
      const data = await roomApi.get(code);
      applyRoom(data);
    } catch {
      toast.error("房间不存在");
    } finally {
      setLoading(false);
    }
  }, [code, applyRoom]);

  useEffect(() => {
    const saved = loadParticipant(code);
    if (saved) {
      setParticipantId(saved.participantId);
      setParticipantName(saved.name);
    }
    void load();
  }, [code, load]);

  useEffect(() => {
    if (!participantId) return;
    const tick = () => {
      roomApi.heartbeat(code, participantId).catch(() => undefined);
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, [code, participantId]);

  const requireParticipant = () => {
    if (!participantId) {
      toast.error("请先通过应用市场加入房间");
      return false;
    }
    return true;
  };

  const roll = async () => {
    if (!requireParticipant()) return;
    diceRollbackRef.current = lastDice;
    const count = parseInt(diceCount, 10);
    const faces = parseInt(diceFaces, 10);
    const optimistic = Array.from({ length: count }, () => Math.floor(Math.random() * faces) + 1);
    setLastDice(optimistic);
    setRolling(true);
    playDiceSound();
    vibrate([20, 30, 20]);
    try {
      const result = await roomApi.rollDice(code, {
        participant_id: participantId!,
        dice_count: count,
        dice_faces: faces,
      });
      setLastDice(result.results);
    } catch {
      setLastDice(diceRollbackRef.current);
      toast.error("摇骰子失败");
    } finally {
      setRolling(false);
    }
  };

  const drawTod = async (category?: "truth" | "dare") => {
    if (!requireParticipant()) return;
    todRollbackRef.current = lastTod;
    setDrawing(true);
    playTodSound();
    vibrate(25);
    try {
      const result = await roomApi.drawTod(code, {
        participant_id: participantId!,
        category,
      });
      setLastTod({ category: result.category, text: result.text });
    } catch {
      setLastTod(todRollbackRef.current);
      toast.error("抽题失败");
    } finally {
      setDrawing(false);
    }
  };

  const nextTurn = async () => {
    if (!requireParticipant()) return;
    try {
      await roomApi.nextTurn(code, { participant_id: participantId! });
    } catch {
      toast.error("轮转失败");
    }
  };

  const addPrompt = async () => {
    if (!participantId || !customPrompt.trim()) return;
    try {
      await roomApi.addPrompt(code, {
        participant_id: participantId,
        category: customCategory,
        text: customPrompt.trim(),
      });
      setCustomPrompt("");
      toast.success("已添加自定义题目");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "添加失败");
    }
  };

  const sendReaction = async (emoji: string) => {
    if (!requireParticipant()) return;
    addFloatingReaction({ emoji, participant_name: participantName ?? undefined });
    try {
      await roomApi.sendReaction(code, { participant_id: participantId!, emoji });
    } catch {
      toast.error("发送表情失败");
    }
  };

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success("房间码已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  const isHost = room?.host_participant_id === participantId;
  const actionsDisabled = connectionStatus === "disconnected" || connectionStatus === "connecting";

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }

  if (!room) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <p className="text-muted-foreground text-sm">房间不存在</p>
        <Button asChild variant="outline">
          <Link href="/marketplace">返回应用市场</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="from-background via-background to-muted/30 min-h-screen bg-gradient-to-br">
      <header className="border-border/70 bg-background/70 sticky top-0 z-20 flex items-center justify-between border-b px-4 py-3 backdrop-blur">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/marketplace">
            <ArrowLeft className="mr-1 size-4" />
            返回
          </Link>
        </Button>
        <div className="text-center">
          <p className="text-foreground text-sm font-semibold">{room.name}</p>
          <button
            type="button"
            onClick={copyCode}
            className="text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 text-xs"
          >
            房间码 {code}
            <Copy className="size-3" />
          </button>
        </div>
        <ConnectionStatusBadge status={connectionStatus} />
      </header>

      <main className="mx-auto grid max-w-5xl gap-4 p-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <InviteCard code={code} participantCount={room.participants.length} />

          {room.current_turn_name ? (
            <Card className="border-primary/30 bg-gradient-to-r from-primary/10 to-violet-500/5">
              <CardContent className="flex flex-wrap items-center gap-2 pt-4">
                <Sparkles className="text-primary size-4" />
                <span className="text-foreground text-sm">
                  当前轮到：<strong>{room.current_turn_name}</strong>
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto"
                  onClick={nextTurn}
                  disabled={actionsDisabled}
                >
                  <SkipForward className="mr-1 size-3.5" />
                  下一位
                </Button>
              </CardContent>
            </Card>
          ) : null}

          <DiceStage
            diceCount={diceCount}
            diceFaces={diceFaces}
            onDiceCountChange={setDiceCount}
            onDiceFacesChange={setDiceFaces}
            rolling={rolling}
            lastDice={lastDice}
            onRoll={roll}
            disabled={actionsDisabled}
          />

          <TodStage
            lastTod={lastTod}
            drawing={drawing}
            onDraw={drawTod}
            disabled={actionsDisabled}
            isHost={isHost}
            customPrompt={customPrompt}
            customCategory={customCategory}
            onCustomPromptChange={setCustomPrompt}
            onCustomCategoryChange={setCustomCategory}
            onAddPrompt={addPrompt}
          />

          <Card>
            <CardContent className="space-y-3 pt-5">
              <p className="text-muted-foreground text-xs">给当前回合来点气氛</p>
              <ReactionBar
                disabled={actionsDisabled}
                onSend={sendReaction}
                floating={floatingReactions}
                onDismiss={(id) =>
                  setFloatingReactions((prev) => prev.filter((item) => item.id !== id))
                }
              />
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <MembersPanel room={room} participantId={participantId} />
          <ActivityFeed feed={feed} />
        </div>
      </main>
    </div>
  );
}
