import { del, get, post, put } from "./fetch";
import type { components, paths } from "./generated/schema";

export type InferenceProvider = components["schemas"]["InferenceProvider"];
export type InferenceModelKind = components["schemas"]["InferenceModelKind"];
export type InferencePurpose = components["schemas"]["InferencePurpose"];
export type InferenceBindingScope = components["schemas"]["InferenceBindingScope"];
export type ResourceVisibility = components["schemas"]["ResourceVisibility"];
export type InferenceCapabilities = components["schemas"]["InferenceCapabilities"];
export type InferenceEndpoint = components["schemas"]["InferenceEndpointResponse"];
export type InferenceEndpointInput = components["schemas"]["InferenceEndpointUpsertRequest"];
export type InferenceModel = components["schemas"]["InferenceModelResponse"];
export type InferenceModelInput = components["schemas"]["InferenceModelUpsertRequest"];
export type InferenceBinding = components["schemas"]["InferenceBindingResponse"];
export type InferenceBindingInput = components["schemas"]["InferenceBindingRequest"];
export type InferenceProbeResult = components["schemas"]["InferenceProbeResponse"];
export type InferenceStatus = components["schemas"]["InferenceStatusResponse"];
export type ChatModelSettings = components["schemas"]["ChatModelSettings"];
export type EmbeddingModelSettings = components["schemas"]["EmbeddingModelSettings"];

type DeleteBindingQuery = NonNullable<
  paths["/api/inference/bindings/{purpose}"]["delete"]["parameters"]["query"]
>;

export const inferenceApi = {
  listEndpoints: (): Promise<components["schemas"]["InferenceEndpointListResponse"]> =>
    get("/inference/endpoints"),
  getEndpoint: (id: string): Promise<InferenceEndpoint> => get(`/inference/endpoints/${id}`),
  createEndpoint: (input: InferenceEndpointInput): Promise<InferenceEndpoint> =>
    post("/inference/endpoints", input),
  updateEndpoint: (id: string, input: InferenceEndpointInput): Promise<InferenceEndpoint> =>
    put(`/inference/endpoints/${id}`, input),
  deleteEndpoint: (id: string): Promise<void> => del(`/inference/endpoints/${id}`),

  listModels: (): Promise<components["schemas"]["InferenceModelListResponse"]> =>
    get("/inference/models"),
  getModel: (id: string): Promise<InferenceModel> => get(`/inference/models/${id}`),
  createModel: (input: InferenceModelInput): Promise<InferenceModel> =>
    post("/inference/models", input),
  updateModel: (id: string, input: InferenceModelInput): Promise<InferenceModel> =>
    put(`/inference/models/${id}`, input),
  deleteModel: (id: string): Promise<void> => del(`/inference/models/${id}`),
  probeModel: (id: string): Promise<InferenceProbeResult> =>
    post(`/inference/models/${id}/probe`, {}),

  listBindings: (): Promise<components["schemas"]["InferenceBindingListResponse"]> =>
    get("/inference/bindings"),
  setBinding: (
    purpose: InferencePurpose,
    input: InferenceBindingInput,
  ): Promise<InferenceBinding> => put(`/inference/bindings/${purpose}`, input),
  deleteBinding: (
    purpose: InferencePurpose,
    bindingScope: InferenceBindingScope,
  ): Promise<void> => {
    const query: DeleteBindingQuery = { binding_scope: bindingScope };
    return del(
      `/inference/bindings/${purpose}?binding_scope=${encodeURIComponent(query.binding_scope ?? "workspace")}`,
    );
  },
  getStatus: (): Promise<InferenceStatus> => get("/inference/status"),
};
