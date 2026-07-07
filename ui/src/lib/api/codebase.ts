import { authenticatedFetch, del, get, parseSSEStream, post } from "./fetch";
import { translate } from "@/i18n/translate";
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
  CodebaseSymbolsData,
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

  listSymbols: (codebaseId: string, name?: string): Promise<CodebaseSymbolsData> => {
    return get<CodebaseSymbolsData>(
      `/codebases/${codebaseId}/symbols`,
      name ? { name } : undefined,
    );
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

  delete: (codebaseId: string): Promise<void> => {
    return del(`/codebases/${codebaseId}`);
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
    onComplete?: () => void,
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

export type { Codebase, CodebaseArtifact };
