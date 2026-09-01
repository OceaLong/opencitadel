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

  /**
   * 获取回收站（已软删除、可恢复）知识库列表
   */
  listDeleted: (): Promise<KnowledgeBasesData> => {
    return get<KnowledgeBasesData>("/knowledge-bases/deleted");
  },

  /**
   * 从回收站恢复知识库
   */
  restore: (kbId: string): Promise<void> => {
    return post<void>(`/knowledge-bases/${kbId}/restore`, {});
  },

  /**
   * 彻底清除回收站中的知识库（不可恢复）
   */
  purge: (kbId: string): Promise<void> => {
    return del<void>(`/knowledge-bases/${kbId}/purge`);
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
    return createIngestStream(
      `/knowledge-bases/${kbId}/ingest`,
      onEvent,
      onError,
      eventId,
      onComplete,
    );
  },
};

export type { KnowledgeBase };
