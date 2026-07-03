import type { KnowledgeSourceType } from "@/lib/api/types";
import { translate } from "@/i18n/translate";

export function parseKbDocHref(value: string): { docId: string; page?: number; chunkId?: string } | null {
  if (!value.startsWith("kbdoc://")) return null;
  const [docId, query = ""] = value.slice("kbdoc://".length).split("?");
  const params = new URLSearchParams(query);
  const page = Number(params.get("page") || 0) || undefined;
  const chunkId = params.get("chunk") || undefined;
  return { docId, page, chunkId };
}

export function inferSourceType(filename: string): KnowledgeSourceType {
  return /\.zip$/i.test(filename) ? "zip" : "upload";
}

export function isStaleRequest(token: number, currentToken: number): boolean {
  return token !== currentToken;
}

export function isChatSendBlocked(sessionId: string | null, loading: boolean): boolean {
  return !sessionId || loading;
}

export function groupFileIdsBySourceType(
  files: Array<{ id: string; sourceType: KnowledgeSourceType }>,
): Record<KnowledgeSourceType, string[]> {
  const groups: Record<KnowledgeSourceType, string[]> = {
    upload: [],
    zip: [],
    web: [],
    confluence: [],
    feishu: [],
  };
  for (const file of files) {
    groups[file.sourceType].push(file.id);
  }
  return groups;
}

export function formatIngestStreamError(data: unknown): string {
  if (data && typeof data === "object" && "error" in data) {
    const message = (data as { error?: string }).error;
    if (message) return message;
  }
  return translate("knowledge.indexFailed");
}
