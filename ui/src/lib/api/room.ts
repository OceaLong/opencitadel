import { get, post } from "./fetch";
import { API_CONFIG } from "./fetch";
import type {
  AddTodPromptParams,
  CreateRoomParams,
  CreateRoomResult,
  DrawTodParams,
  JoinRoomParams,
  JoinRoomResult,
  NextTurnParams,
  RollDiceParams,
  RoomData,
} from "./types";

export const roomApi = {
  create: (params: CreateRoomParams): Promise<CreateRoomResult> =>
    post<CreateRoomResult>("/rooms", params),

  join: (code: string, params: JoinRoomParams): Promise<JoinRoomResult> =>
    post<JoinRoomResult>(`/rooms/${code}/join`, params),

  get: (code: string): Promise<RoomData> => get<RoomData>(`/rooms/${code}`),

  heartbeat: (code: string, participantId: string): Promise<{ ok: boolean }> =>
    post<{ ok: boolean }>(`/rooms/${code}/heartbeat`, { participant_id: participantId }),

  rollDice: (
    code: string,
    params: RollDiceParams,
  ): Promise<{ results: number[]; total: number; event_id: string }> =>
    post(`/rooms/${code}/dice`, {
      participant_id: params.participant_id,
      dice_count: params.dice_count ?? 1,
      dice_faces: params.dice_faces ?? 6,
    }),

  drawTod: (
    code: string,
    params: DrawTodParams,
  ): Promise<{ category: string; text: string; event_id: string }> =>
    post(`/rooms/${code}/tod`, {
      participant_id: params.participant_id,
      category: params.category,
    }),

  nextTurn: (code: string, params: NextTurnParams): Promise<Record<string, unknown>> =>
    post(`/rooms/${code}/turn`, { participant_id: params.participant_id }),

  addPrompt: (code: string, params: AddTodPromptParams): Promise<{ id: string }> =>
    post(`/rooms/${code}/prompts`, {
      participant_id: params.participant_id,
      category: params.category,
      text: params.text,
    }),

  sendReaction: (
    code: string,
    params: { participant_id: string; emoji: string },
  ): Promise<Record<string, unknown>> =>
    post(`/rooms/${code}/reaction`, params),

  streamUrl: (code: string): string =>
    `${API_CONFIG.baseURL}/rooms/${code}/stream`,
};
