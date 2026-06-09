"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Copy,
  Dices,
  Loader2,
  MessageCircle,
  SkipForward,
  Sparkles,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { loadParticipant } from "@/components/marketplace/room-app";
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
import { roomApi } from "@/lib/api/room";
import type { RoomData, RoomEvent } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export default function RoomPage() {
  const params = useParams();
  const code = (params.code as string).toUpperCase();

  const [room, setRoom] = useState<RoomData | null>(null);
  const [participantId, setParticipantId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [rolling, setRolling] = useState(false);
  const [lastDice, setLastDice] = useState<number[] | null>(null);
  const [lastTod, setLastTod] = useState<{ category: string; text: string } | null>(null);
  const [diceCount, setDiceCount] = useState("1");
  const [diceFaces, setDiceFaces] = useState("6");
  const [customPrompt, setCustomPrompt] = useState("");
  const [customCategory, setCustomCategory] = useState<"truth" | "dare">("truth");
  const [feed, setFeed] = useState<RoomEvent[]>([]);

  const esRef = useRef<EventSource | null>(null);

  const applyRoom = useCallback((data: RoomData) => {
    setRoom(data);
    if (data.recent_events) {
      setFeed(data.recent_events.slice(-20));
    }
  }, []);

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
    if (saved) setParticipantId(saved.participantId);
    load();
  }, [code, load]);

  useEffect(() => {
    const url = roomApi.streamUrl(code);
    const es = new EventSource(url);
    esRef.current = es;

    es.addEventListener("snapshot", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.room) applyRoom(data.room);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("room_event", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.room) applyRoom(data.room);
        if (data.type === "dice" && data.payload?.results) {
          setLastDice(data.payload.results as number[]);
        }
        if (data.type === "tod_draw" && data.payload) {
          setLastTod({
            category: data.payload.category as string,
            text: data.payload.text as string,
          });
        }
        if (data.type === "turn" && data.payload && !data.room) {
          setRoom((prev) =>
            prev
              ? {
                  ...prev,
                  current_turn_index: data.payload.current_turn_index as number,
                  current_turn_id: data.payload.current_turn_id as string,
                  current_turn_name: data.payload.current_turn_name as string,
                }
              : prev,
          );
        }
        if (data.payload) {
          setFeed((prev) => [
            ...prev.slice(-19),
            {
              id: (data.event_id as string) || `${Date.now()}`,
              type: data.type,
              payload: data.payload,
            },
          ]);
        }
      } catch {
        /* ignore */
      }
    });

    es.onerror = () => {
      /* EventSource auto-reconnects */
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [code, applyRoom]);

  useEffect(() => {
    if (!participantId) return;
    const tick = () => {
      roomApi.heartbeat(code, participantId).catch(() => undefined);
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, [code, participantId]);

  const roll = async () => {
    if (!participantId) {
      toast.error("请先通过应用市场加入房间");
      return;
    }
    setRolling(true);
    try {
      const result = await roomApi.rollDice(code, {
        participant_id: participantId,
        dice_count: parseInt(diceCount, 10),
        dice_faces: parseInt(diceFaces, 10),
      });
      setLastDice(result.results);
    } catch {
      toast.error("摇骰子失败");
    } finally {
      setRolling(false);
    }
  };

  const drawTod = async (category?: "truth" | "dare") => {
    if (!participantId) return;
    try {
      const result = await roomApi.drawTod(code, {
        participant_id: participantId,
        category,
      });
      setLastTod({ category: result.category, text: result.text });
    } catch {
      toast.error("抽题失败");
    }
  };

  const nextTurn = async () => {
    if (!participantId) return;
    try {
      await roomApi.nextTurn(code, { participant_id: participantId });
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

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success("房间码已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  const isHost = room?.host_participant_id === participantId;

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
      <header className="border-border/70 bg-background/70 flex items-center justify-between border-b px-4 py-3 backdrop-blur">
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
            className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs"
          >
            房间码 {code}
            <Copy className="size-3" />
          </button>
        </div>
        <div className="w-16" />
      </header>

      <main className="mx-auto grid max-w-4xl gap-4 p-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {room.current_turn_name && (
            <Card className="border-primary/30 bg-primary/5">
              <CardContent className="flex items-center gap-2 pt-4">
                <Sparkles className="text-primary size-4" />
                <span className="text-foreground text-sm">
                  当前轮到：<strong>{room.current_turn_name}</strong>
                </span>
                <Button size="sm" variant="outline" className="ml-auto" onClick={nextTurn}>
                  <SkipForward className="mr-1 size-3.5" />
                  下一位
                </Button>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent className="space-y-4 pt-5">
              <div className="flex items-center gap-2">
                <Dices className="text-primary size-5" />
                <h2 className="text-foreground text-sm font-semibold">摇骰子</h2>
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <Label className="text-xs">个数</Label>
                  <Select value={diceCount} onValueChange={setDiceCount}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {["1", "2", "3", "4", "5", "6"].map((n) => (
                        <SelectItem key={n} value={n}>{n} 个</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex-1">
                  <Label className="text-xs">面数</Label>
                  <Select value={diceFaces} onValueChange={setDiceFaces}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {["6", "8", "10", "12", "20"].map((n) => (
                        <SelectItem key={n} value={n}>D{n}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <Button className="w-full" onClick={roll} disabled={rolling}>
                {rolling ? <Loader2 className="size-4 animate-spin" /> : "摇！"}
              </Button>
              {lastDice && (
                <div className="flex flex-wrap justify-center gap-3 py-2">
                  {lastDice.map((v, i) => (
                    <div
                      key={i}
                      className="bg-primary text-primary-foreground flex size-14 animate-bounce items-center justify-center rounded-xl text-2xl font-bold shadow-lg"
                      style={{ animationDelay: `${i * 100}ms` }}
                    >
                      {v}
                    </div>
                  ))}
                  <p className="text-muted-foreground w-full text-center text-xs">
                    合计 {lastDice.reduce((a, b) => a + b, 0)}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-4 pt-5">
              <div className="flex items-center gap-2">
                <MessageCircle className="text-rose-500 size-5" />
                <h2 className="text-foreground text-sm font-semibold">真心话大冒险</h2>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => drawTod("truth")}>
                  真心话
                </Button>
                <Button variant="outline" className="flex-1" onClick={() => drawTod("dare")}>
                  大冒险
                </Button>
                <Button className="flex-1" onClick={() => drawTod()}>
                  随机
                </Button>
              </div>
              {lastTod && (
                <div className="bg-muted/50 rounded-xl p-4 text-center">
                  <span className="text-xs font-medium uppercase tracking-wide text-rose-500">
                    {lastTod.category === "truth" ? "真心话" : "大冒险"}
                  </span>
                  <p className="text-foreground mt-2 text-sm leading-relaxed">{lastTod.text}</p>
                </div>
              )}
              {isHost && (
                <div className="space-y-2 border-t pt-3">
                  <Label className="text-xs">房主：添加自定义题目</Label>
                  <Select
                    value={customCategory}
                    onValueChange={(v) => setCustomCategory(v as "truth" | "dare")}
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="truth">真心话</SelectItem>
                      <SelectItem value="dare">大冒险</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    placeholder="输入自定义题目"
                  />
                  <Button variant="outline" size="sm" onClick={addPrompt}>
                    添加
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardContent className="space-y-3 pt-5">
              <div className="flex items-center gap-2">
                <Users className="text-muted-foreground size-4" />
                <h3 className="text-foreground text-sm font-semibold">
                  在线成员 ({room.participants.filter((p) => p.online).length}/{room.participants.length})
                </h3>
              </div>
              <ul className="space-y-2">
                {room.participants.map((p) => (
                  <li
                    key={p.id}
                    className={cn(
                      "flex items-center gap-2 rounded-lg px-3 py-2 text-sm",
                      p.id === room.current_turn_id && "bg-primary/10 ring-primary/30 ring-1",
                      p.id === participantId && "font-medium",
                    )}
                  >
                    <span
                      className={cn(
                        "size-2 rounded-full",
                        p.online ? "bg-emerald-500" : "bg-muted-foreground/40",
                      )}
                    />
                    {p.name}
                    {p.id === room.host_participant_id && (
                      <span className="text-muted-foreground text-[10px]">房主</span>
                    )}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-2 pt-5">
              <h3 className="text-foreground text-sm font-semibold">动态</h3>
              <ul className="max-h-64 space-y-1 overflow-auto text-xs">
                {feed.slice().reverse().map((ev) => (
                  <li key={ev.id} className="text-muted-foreground border-b border-dashed py-1.5">
                    {ev.type === "join" && `${ev.payload.name ?? "有人"} 加入了房间`}
                    {ev.type === "dice" &&
                      `${ev.payload.participant_name ?? "有人"} 摇了 ${(ev.payload.results as number[])?.join("+")}`}
                    {ev.type === "tod_draw" &&
                      `${ev.payload.participant_name ?? "有人"} 抽到${ev.payload.category === "truth" ? "真心话" : "大冒险"}`}
                    {ev.type === "turn" && `轮到 ${ev.payload.current_turn_name ?? "下一位"}`}
                    {ev.type === "prompt_add" && `房主添加了自定义题目`}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
