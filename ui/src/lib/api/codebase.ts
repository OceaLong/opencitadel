import { authenticatedFetch, get, parseSSEStream, post } from "./fetch";
import type {
  Codebase,
  CodebaseArtifact,
  CodebaseArtifactsData,
  CodebasesData,
  CodebaseSessionData,
  CreateCodebaseParams,
  CreateCodebaseSessionParams,
  DownloadCodebaseData,
  FileTreeData,
  ReadSourceData,
  ReadSourceParams,
  SSEEventData,
  SSEEventHandler,
} from "./types";

export const codebaseApi = {
  create: (params: CreateCodebaseParams): Promise<Codebase> => {
    return post<Codebase>("/codebases", params);
  },

  list: (limit = 100, offset = 0): Promise<CodebasesData> => {
    return get<CodebasesData>("/codebases", { limit, offset });
  },

  get: (codebaseId: string): Promise<Codebase> => {
    return get<Codebase>(`/codebases/${codebaseId}`);
  },

  getTree: (codebaseId: string): Promise<FileTreeData> => {
    return get<FileTreeData>(`/codebases/${codebaseId}/tree`);
  },

  getArtifacts: (codebaseId: string, kind?: string): Promise<CodebaseArtifactsData> => {
    return get<CodebaseArtifactsData>(
      `/codebases/${codebaseId}/artifacts`,
      kind ? { kind } : undefined,
    );
  },

  readSource: (codebaseId: string, params: ReadSourceParams): Promise<ReadSourceData> => {
    return post<ReadSourceData>(`/codebases/${codebaseId}/source`, params);
  },

  reanalyze: (codebaseId: string): Promise<Codebase> => {
    return post<Codebase>(`/codebases/${codebaseId}/reanalyze`);
  },

  download: (codebaseId: string): Promise<DownloadCodebaseData> => {
    return get<DownloadCodebaseData>(`/codebases/${codebaseId}/download`);
  },

  createSession: (
    codebaseId: string,
    params?: CreateCodebaseSessionParams,
  ): Promise<CodebaseSessionData> => {
    return post<CodebaseSessionData>(`/codebases/${codebaseId}/sessions`, params || {});
  },

  ingestStream: (
    codebaseId: string,
    onEvent: SSEEventHandler,
    onError?: (error: Error) => void,
    eventId?: string,
  ): () => void => {
    const controller = new AbortController();
    const url = `/codebases/${codebaseId}/ingest${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ""}`;

    const start = async () => {
      try {
        const response = await authenticatedFetch(url, {
          method: "GET",
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`摄取流连接失败: ${response.status}`);
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

export type { Codebase, CodebaseArtifact };
