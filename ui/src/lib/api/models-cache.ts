import { modelsApi } from "./models";
import type { LLMModel, LLMProvider } from "./types";

const SUPPORTED_PROVIDER_VALUES: LLMProvider[] = ["openai", "ollama", "azure"];

const MODELS_CACHE_INVALIDATED = "models-cache-invalidated";

let modelsCache: LLMModel[] | null = null;
let modelsPromise: Promise<LLMModel[]> | null = null;

export function filterSupportedModels(models: LLMModel[]): LLMModel[] {
  const supportedSet = new Set(SUPPORTED_PROVIDER_VALUES);
  return models.filter((m) => supportedSet.has(m.provider));
}

export function resolveDefaultModelId(models: LLMModel[]): string | undefined {
  const supported = filterSupportedModels(models);
  return (supported.find((m) => m.is_default) ?? supported[0])?.id;
}

export function loadModels(): Promise<LLMModel[]> {
  if (modelsCache) return Promise.resolve(modelsCache);
  if (!modelsPromise) {
    modelsPromise = modelsApi.list().then((data) => {
      modelsCache = data.models;
      modelsPromise = null;
      return data.models;
    });
  }
  return modelsPromise;
}

export function invalidateModelsCache(): void {
  modelsCache = null;
  modelsPromise = null;
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(MODELS_CACHE_INVALIDATED));
  }
}

export function onModelsCacheInvalidated(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener(MODELS_CACHE_INVALIDATED, listener);
  return () => window.removeEventListener(MODELS_CACHE_INVALIDATED, listener);
}
