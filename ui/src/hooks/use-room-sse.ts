"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { loadParticipant, saveParticipant } from "@/components/marketplace/room-app";
import { roomApi } from "@/lib/api/room";
import type { RoomData, RoomEvent } from "@/lib/api/types";

export type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

type UseRoomSSEOptions = {
  code: string;
  participantId: string | null;
  participantName: string | null;
  onRoom: (room: RoomData) => void;
  onDice?: (results: number[]) => void;
  onTod?: (data: { category: string; text: string }) => void;
  onTurn?: (payload: Record<string, unknown>) => void;
  onEvent?: (event: RoomEvent) => void;
  onReaction?: (payload: { emoji: string; participant_name?: string }) => void;
};

export function useRoomSSE({
  code,
  participantId,
  participantName,
  onRoom,
  onDice,
  onTod,
  onTurn,
  onEvent,
  onReaction,
}: UseRoomSSEOptions) {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const esRef = useRef<EventSource | null>(null);
  const reconnectAttemptRef = useRef(0);
  const recoveredRef = useRef(false);

  const recoverParticipant = useCallback(async () => {
    const saved = loadParticipant(code);
    if (!saved?.name) return null;
    try {
      const result = await roomApi.join(code, { name: saved.name });
      saveParticipant(result.room.code, result.participant_id, saved.name);
      onRoom(result.room);
      if (!recoveredRef.current) {
        recoveredRef.current = true;
        toast.success("已重新加入房间");
      }
      return result.participant_id;
    } catch {
      return null;
    }
  }, [code, onRoom]);

  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const refreshSnapshot = async () => {
      try {
        const data = await roomApi.get(code);
        onRoom(data);
      } catch {
        /* ignore */
      }
    };

    const connect = () => {
      if (cancelled) return;
      const url = roomApi.streamUrl(code);
      const es = new EventSource(url);
      esRef.current = es;

      es.addEventListener("open", () => {
        reconnectAttemptRef.current = 0;
        setConnectionStatus("connected");
      });

      es.addEventListener("snapshot", (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.room) onRoom(data.room);
        } catch {
          /* ignore */
        }
      });

      es.addEventListener("room_event", (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.room) onRoom(data.room);
          if (data.type === "dice" && data.payload?.results) {
            onDice?.(data.payload.results as number[]);
          }
          if (data.type === "tod_draw" && data.payload) {
            onTod?.({
              category: data.payload.category as string,
              text: data.payload.text as string,
            });
          }
          if (data.type === "turn" && data.payload) {
            onTurn?.(data.payload as Record<string, unknown>);
          }
          if (data.type === "reaction" && data.payload) {
            onReaction?.(data.payload as { emoji: string; participant_name?: string });
          }
          if (data.payload) {
            onEvent?.({
              id: (data.event_id as string) || `${Date.now()}`,
              type: data.type,
              payload: data.payload,
            });
          }
        } catch {
          /* ignore */
        }
      });

      es.addEventListener("error", () => {
        setConnectionStatus("reconnecting");
        es.close();
        esRef.current = null;
        reconnectAttemptRef.current += 1;
        const delay = Math.min(1000 * 2 ** reconnectAttemptRef.current, 15000);
        reconnectTimer = setTimeout(async () => {
          if (cancelled) return;
          await refreshSnapshot();
          if (!cancelled) connect();
        }, delay);
      });
    };

    setConnectionStatus("connecting");
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      esRef.current?.close();
      esRef.current = null;
    };
  }, [
    code,
    onRoom,
    onDice,
    onTod,
    onTurn,
    onEvent,
    onReaction,
    participantId,
    participantName,
    recoverParticipant,
  ]);

  return { connectionStatus, recoverParticipant, refreshSnapshot: async () => roomApi.get(code) };
}
