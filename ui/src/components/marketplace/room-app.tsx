"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Dices, DoorOpen, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { roomApi } from "@/lib/api/room";

const PARTICIPANT_KEY = "my-manus-room-participant";

export function saveParticipant(code: string, participantId: string, name: string) {
  localStorage.setItem(
    PARTICIPANT_KEY,
    JSON.stringify({ code: code.toUpperCase(), participantId, name }),
  );
}

export function loadParticipant(code: string): { participantId: string; name: string } | null {
  try {
    const raw = localStorage.getItem(PARTICIPANT_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data.code === code.toUpperCase()) return data;
    return null;
  } catch {
    return null;
  }
}

type RoomAppProps = {
  initialCode?: string;
};

export function RoomApp({ initialCode }: RoomAppProps) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [roomName, setRoomName] = useState("真心话大冒险");
  const [joinCode, setJoinCode] = useState(initialCode ?? "");
  const [loading, setLoading] = useState(false);

  const create = async () => {
    if (!name.trim()) {
      toast.error("请输入昵称");
      return;
    }
    setLoading(true);
    try {
      const result = await roomApi.create({ name: roomName, host_name: name.trim() });
      saveParticipant(result.room.code, result.participant_id, name.trim());
      router.push(`/room/${result.room.code}`);
    } catch {
      toast.error("创建房间失败");
    } finally {
      setLoading(false);
    }
  };

  const join = async () => {
    if (!name.trim()) {
      toast.error("请输入昵称");
      return;
    }
    if (!joinCode.trim()) {
      toast.error("请输入房间码");
      return;
    }
    setLoading(true);
    try {
      const result = await roomApi.join(joinCode.trim().toUpperCase(), {
        name: name.trim(),
      });
      saveParticipant(result.room.code, result.participant_id, name.trim());
      router.push(`/room/${result.room.code}`);
    } catch {
      toast.error("加入房间失败，请检查房间码");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-md space-y-6">
      <div className="text-center">
        <Dices className="text-primary mx-auto size-12" />
        <h2 className="text-foreground mt-3 text-lg font-semibold">派对房间</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          创建或加入房间，摇骰子、真心话大冒险
        </p>
      </div>

      <div className="space-y-2">
        <Label>你的昵称</Label>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="输入昵称" />
      </div>

      <Card>
        <CardContent className="space-y-3 pt-5">
          <Label>创建房间</Label>
          <Input
            value={roomName}
            onChange={(e) => setRoomName(e.target.value)}
            placeholder="房间名称"
          />
          <Button className="w-full" onClick={create} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : <Dices className="mr-1 size-4" />}
            创建房间
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-3 pt-5">
          <Label>加入房间</Label>
          <Input
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
            placeholder="输入 6 位房间码"
            maxLength={6}
          />
          <Button variant="outline" className="w-full" onClick={join} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : <DoorOpen className="mr-1 size-4" />}
            加入房间
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
