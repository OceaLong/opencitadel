import { del, get, post, put } from "./fetch";
import type {
  CreateLLMEndpointParams,
  LLMEndpoint,
  LLMEndpointsData,
} from "./types";

export const endpointsApi = {
  list: (): Promise<LLMEndpointsData> => get<LLMEndpointsData>("/llm-endpoints"),

  get: (id: string): Promise<LLMEndpoint> => get<LLMEndpoint>(`/llm-endpoints/${id}`),

  create: (params: CreateLLMEndpointParams): Promise<LLMEndpoint> =>
    post<LLMEndpoint>("/llm-endpoints", params),

  update: (id: string, params: Partial<CreateLLMEndpointParams>): Promise<LLMEndpoint> =>
    put<LLMEndpoint>(`/llm-endpoints/${id}`, params),

  delete: (id: string): Promise<void> => del<void>(`/llm-endpoints/${id}`),
};
