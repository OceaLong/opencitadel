import { get, post, put, del } from "./fetch";
import type { LLMModel, LLMModelsData, CreateLLMModelParams } from "./types";

export const modelsApi = {
  list: (): Promise<LLMModelsData> => get<LLMModelsData>("/llm-models"),

  get: (id: string): Promise<LLMModel> => get<LLMModel>(`/llm-models/${id}`),

  create: (params: CreateLLMModelParams): Promise<LLMModel> =>
    post<LLMModel>("/llm-models", params),

  update: (id: string, params: Partial<CreateLLMModelParams>): Promise<LLMModel> =>
    put<LLMModel>(`/llm-models/${id}`, params),

  delete: (id: string): Promise<void> => del<void>(`/llm-models/${id}`),

  setDefault: (id: string): Promise<LLMModel> =>
    post<LLMModel>(`/llm-models/${id}/set-default`, {}),
};
