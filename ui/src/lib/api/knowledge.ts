import { createIngestStream, del, get, post } from "./fetch";
import { makeResourceClient } from "./resource-client";
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
  SSEEventHandler,
} from "./types";

const knowledgeResourceClient = makeResourceClient<
  KnowledgeBase,
  KnowledgeBasesData,
  KnowledgeVersion,
  KnowledgeVersionsData,
  KnowledgeBuild,
  CreateKnowledgeBaseParams,
  CreateKnowledgeSessionParams,
  KnowledgeSessionData
>("/knowledge-bases");

export const knowledgeApi = {
  ...knowledgeResourceClient,

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
    return createIngestStream(`/knowledge-bases/${kbId}/ingest`, onEvent, onError, eventId, onComplete);
  },
};

export type { KnowledgeBase };
