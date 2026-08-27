import type { ClientDataScope } from "../data/client-data-scope";
import type {
  InferenceBinding,
  InferenceEndpoint,
  InferenceModel,
  InferencePurpose,
} from "./inference";
import { inferenceApi } from "./inference";

export type InferenceSnapshot = {
  endpoints: InferenceEndpoint[];
  models: InferenceModel[];
  bindings: InferenceBinding[];
};

export async function loadInferenceSnapshot(scope: ClientDataScope): Promise<InferenceSnapshot> {
  void scope;
  const [endpoints, models, bindings] = await Promise.all([
    inferenceApi.listEndpoints(),
    inferenceApi.listModels(),
    inferenceApi.listBindings(),
  ]);
  return {
    endpoints: endpoints.items ?? [],
    models: models.items ?? [],
    bindings: bindings.items ?? [],
  };
}

export function modelsForPurpose(
  models: InferenceModel[],
  purpose: InferencePurpose,
): InferenceModel[] {
  const expectedKind = purpose === "embedding" ? "embedding" : "chat";
  return models.filter((model) => model.kind === expectedKind);
}

export function boundModelId(
  bindings: InferenceBinding[],
  purpose: InferencePurpose,
): string | undefined {
  return bindings.find((binding) => binding.purpose === purpose)?.model_id;
}

export function bindingIsInherited(binding: InferenceBinding | undefined): boolean {
  return Boolean(binding && !binding.owner_user_id && !binding.team_id);
}

export function bindingSelectionValue(binding: InferenceBinding | undefined): string {
  return bindingIsInherited(binding) ? "inherit" : (binding?.model_id ?? "inherit");
}
