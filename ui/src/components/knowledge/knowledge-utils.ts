import type { KnowledgeDocument, KnowledgeSourceType } from "@/lib/api/types";

import { translate } from "@/i18n/translate";

export function parseKbDocHref(value: string): {
  docId: string;
  page?: number;
  chunkId?: string;
  versionId?: string;
  revisionId?: string;
} | null {
  if (!value.startsWith("kbdoc://")) return null;
  const raw = value.slice("kbdoc://".length);
  const queryIndex = raw.indexOf("?");
  const docId = queryIndex === -1 ? raw : raw.slice(0, queryIndex);
  const query = queryIndex === -1 ? "" : raw.slice(queryIndex + 1);
  if (!docId || docId.trim() !== docId || /[\s/?#]/.test(docId)) return null;
  const params = new URLSearchParams(query);
  const identityKeys = ["page", "chunk", "version", "revision"] as const;
  if (identityKeys.some((key) => params.getAll(key).length > 1)) return null;
  const pageValue = params.get("page");
  if (
    pageValue !== null &&
    (!/^[1-9]\d*$/.test(pageValue) || !Number.isSafeInteger(Number(pageValue)))
  )
    return null;
  const page = pageValue === null ? undefined : Number(pageValue);
  const optionalIdentity = (key: "chunk" | "version" | "revision") => {
    const item = params.get(key);
    if (item === null) return undefined;
    if (!item || item.trim() !== item || /\s/.test(item)) return null;
    return item;
  };
  const chunkId = optionalIdentity("chunk");
  const versionId = optionalIdentity("version");
  const revisionId = optionalIdentity("revision");
  if (chunkId === null || versionId === null || revisionId === null) {
    return null;
  }
  return {
    docId,
    ...(page ? { page } : {}),
    ...(chunkId ? { chunkId } : {}),
    ...(versionId ? { versionId } : {}),
    ...(revisionId ? { revisionId } : {}),
  };
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

export function canStartAsk(kb: { ready_doc_count?: number }): boolean {
  return (kb.ready_doc_count ?? 0) > 0;
}

export function appendDocumentsPage(
  prev: KnowledgeDocument[],
  incoming: KnowledgeDocument[],
): KnowledgeDocument[] {
  const seen = new Set(prev.map((doc) => doc.id));
  return [...prev, ...incoming.filter((doc) => !seen.has(doc.id))];
}
