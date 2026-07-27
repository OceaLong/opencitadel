import { del, get, post, put } from "./fetch";
import type { CreateLLMModelParams, LLMModel, LLMModelsData, MultimodalProbeResult } from "./types";

function modelMutationPayload(params: Partial<CreateLLMModelParams>) {
  const payload = { ...params };
  delete payload.is_default;
  return payload;
}

export const modelsApi = {
  list: (): Promise<LLMModelsData> => get<LLMModelsData>("/llm-models"),

  get: (id: string): Promise<LLMModel> => get<LLMModel>(`/llm-models/${id}`),

  create: (params: CreateLLMModelParams): Promise<LLMModel> =>
    post<LLMModel>("/llm-models", modelMutationPayload(params)),

  update: (id: string, params: Partial<CreateLLMModelParams>): Promise<LLMModel> =>
    put<LLMModel>(`/llm-models/${id}`, modelMutationPayload(params)),

  delete: (id: string): Promise<void> => del<void>(`/llm-models/${id}`),

  setDefault: (id: string): Promise<LLMModel> =>
    post<LLMModel>(`/llm-models/${id}/set-default`, {}),

  setPreferred: (id: string): Promise<LLMModel> =>
    post<LLMModel>(`/llm-models/${id}/set-preferred`, {}),

  probeMultimodal: (id: string): Promise<MultimodalProbeResult> =>
    post<MultimodalProbeResult>(`/llm-models/${id}/probe-multimodal`, {}),
};
