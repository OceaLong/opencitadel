import { get } from "./fetch";
import type { LLMStatusData } from "./types";
import { translate } from "@/i18n/translate";

export const llmStatusApi = {
  getStatus: (): Promise<LLMStatusData> => get<LLMStatusData>("/llm/status"),
};

export function isModelUnavailableStatus(status: LLMStatusData["status"] | undefined): boolean {
  return status === "not_configured" || status === "degraded";
}

export function modelErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  if (code === "MODEL_QUOTA_EXCEEDED") {
    return translate("errors.modelQuotaExceeded");
  }
  if (code === "MODEL_INVALID_REQUEST") {
    return translate("errors.modelInvalidRequest");
  }
  if (code.startsWith("MODEL_") || code.startsWith("EMBEDDING_")) {
    return translate("errors.modelUnavailable");
  }
  if (code === "TASK_INFRA_FAILED") {
    return translate("errors.infraFailed");
  }
  return null;
}
