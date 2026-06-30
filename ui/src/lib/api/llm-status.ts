import { get } from "./fetch";
import type { LLMStatusData } from "./types";

export const llmStatusApi = {
  getStatus: (): Promise<LLMStatusData> => get<LLMStatusData>("/llm/status"),
};

export function isModelUnavailableStatus(status: LLMStatusData["status"] | undefined): boolean {
  return status === "not_configured" || status === "degraded";
}

export function modelErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  if (code.startsWith("MODEL_") || code.startsWith("EMBEDDING_")) {
    return "模型或向量服务暂不可用，请检查模型设置或稍后重试";
  }
  if (code === "TASK_INFRA_FAILED") {
    return "系统基础设施异常，请稍后重试或联系管理员";
  }
  return null;
}
