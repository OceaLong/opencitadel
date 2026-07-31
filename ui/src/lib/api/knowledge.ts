import { translate } from "@/i18n/translate";

import { authenticatedFetch, del, get, parseSSEStream, post } from "./fetch";
import type {
  AddKnowledgeDocumentsParams,
  CreateKnowledgeBaseParams,
  CreateKnowledgeSessionParams,
  KnowledgeBase,
  KnowledgeBasesData,
  KnowledgeBuild,
  KnowledgeDocumentsData,
  KnowledgeGraphData,
  KnowledgeSessionData,
  KnowledgeVersion,
  KnowledgeVersionsData,
  ReadKnowledgeDocumentData,
  SSEEventData,
  SSEEventHandler,
} from "./types";

export const knowledgeApi = {
  create: (params: CreateKnowledgeBaseParams): Promise<KnowledgeBase> => {
    return post<KnowledgeBase>("/knowledge-bases", params);
  },

  list: (limit = 100, offset = 0): Promise<KnowledgeBasesData> => {
    return get<KnowledgeBasesData>("/knowledge-bases", { limit, offset });
  },

  get: (kbId: string): Promise<KnowledgeBase> => {
    return get<KnowledgeBase>(`/knowledge-bases/${kbId}`);
  },

  listVersions: (kbId: string): Promise<KnowledgeVersionsData> => {
    return get<KnowledgeVersionsData>(`/knowledge-bases/${kbId}/versions`);
  },

  getVersion: (kbId: string, versionId: string): Promise<KnowledgeVersion> => {
    return get<KnowledgeVersion>(`/knowledge-bases/${kbId}/versions/${versionId}`);
  },

  createBuild: (kbId: string): Promise<KnowledgeVersion> => {
    return post<KnowledgeVersion>(`/knowledge-bases/${kbId}/builds`);
  },

  retryBuild: (kbId: string, buildId: string): Promise<KnowledgeVersion> => {
    return post<KnowledgeVersion>(`/knowledge-bases/${kbId}/builds/${buildId}/retry`);
  },

  cancelBuild: (kbId: string, buildId: string): Promise<KnowledgeBuild> => {
    return post<KnowledgeBuild>(`/knowledge-bases/${kbId}/builds/${buildId}/cancel`);
  },

  delete: (kbId: string): Promise<void> => {
    return del(`/knowledge-bases/${kbId}`);
  },

  addDocuments: (kbId: string, params: AddKnowledgeDocumentsParams): Promise<KnowledgeBase> => {
    return post<KnowledgeBase>(`/knowledge-bases/${kbId}/documents`, params);
  },

  listDocuments: (kbId: string, limit = 50, offset = 0): Promise<KnowledgeDocumentsData> => {
    return get<KnowledgeDocumentsData>(`/knowledge-bases/${kbId}/documents`, { limit, offset });
  },

  deleteDocument: (kbId: string, docId: string): Promise<KnowledgeBase> => {
    return del<KnowledgeBase>(`/knowledge-bases/${kbId}/documents/${docId}`);
  },

  reindex: (kbId: string): Promise<KnowledgeBase> => {
    return post<KnowledgeBase>(`/knowledge-bases/${kbId}/reindex`);
  },

  createSession: (
    kbId: string,
    params?: CreateKnowledgeSessionParams,
  ): Promise<KnowledgeSessionData> => {
    return post<KnowledgeSessionData>(`/knowledge-bases/${kbId}/sessions`, params || {});
  },

  readDocument: (
    kbId: string,
    docId: string,
    page?: number,
  ): Promise<ReadKnowledgeDocumentData> => {
    return get<ReadKnowledgeDocumentData>(
      `/knowledge-bases/${kbId}/documents/${docId}`,
      page ? { page } : undefined,
    );
  },

  readDocumentPage: (
    kbId: string,
    versionId: string,
    docId: string,
    params?: { page?: number; cursor?: string; limit?: number },
  ): Promise<ReadKnowledgeDocumentData> => {
    return get<ReadKnowledgeDocumentData>(
      `/knowledge-bases/${kbId}/versions/${versionId}/documents/${docId}/content`,
      params,
    );
  },

  getGraph: (
    kbId: string,
    versionId: string,
    params?: { q?: string; cursor?: string; limit?: number },
  ): Promise<KnowledgeGraphData> => {
    return get<KnowledgeGraphData>(`/knowledge-bases/${kbId}/versions/${versionId}/graph`, params);
  },

  ingestStream: (
    kbId: string,
    onEvent: SSEEventHandler,
    onError?: (error: Error) => void,
    eventId?: string,
    onComplete?: () => void,
  ): (() => void) => {
    const controller = new AbortController();
    const url = `/knowledge-bases/${kbId}/ingest${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ""}`;

    const start = async () => {
      try {
        const response = await authenticatedFetch(url, {
          method: "GET",
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(
            translate("errors.ingestStreamConnectionFailed", { status: String(response.status) }),
          );
        }
        await parseSSEStream(
          response.body,
          (messageEvent) => {
            const data =
              typeof messageEvent.data === "string"
                ? JSON.parse(messageEvent.data)
                : messageEvent.data;
            onEvent({
              type: messageEvent.type as SSEEventData["type"],
              data,
            } as SSEEventData);
          },
          onError,
        );
        onComplete?.();
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          onError?.(err as Error);
        }
      }
    };
    void start();
    return () => controller.abort();
  },
};

export type { KnowledgeBase };
