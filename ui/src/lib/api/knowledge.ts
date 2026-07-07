import { authenticatedFetch, del, get, parseSSEStream, post } from "./fetch";
import { translate } from "@/i18n/translate";
import type {
  AddKnowledgeDocumentsParams,
  CreateKnowledgeBaseParams,
  CreateKnowledgeSessionParams,
  KnowledgeBase,
  KnowledgeBasesData,
  KnowledgeDocumentsData,
  KnowledgeSessionData,
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

  delete: (kbId: string): Promise<void> => {
    return del(`/knowledge-bases/${kbId}`);
  },

  addDocuments: (kbId: string, params: AddKnowledgeDocumentsParams): Promise<KnowledgeBase> => {
    return post<KnowledgeBase>(`/knowledge-bases/${kbId}/documents`, params);
  },

  listDocuments: (kbId: string): Promise<KnowledgeDocumentsData> => {
    return get<KnowledgeDocumentsData>(`/knowledge-bases/${kbId}/documents`);
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

  readDocument: (kbId: string, docId: string, page?: number): Promise<ReadKnowledgeDocumentData> => {
    return get<ReadKnowledgeDocumentData>(
      `/knowledge-bases/${kbId}/documents/${docId}`,
      page ? { page } : undefined,
    );
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
