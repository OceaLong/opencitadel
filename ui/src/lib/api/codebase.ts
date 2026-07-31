import { createIngestStream, get, post } from "./fetch";
import { makeResourceClient } from "./resource-client";
import type {
  Codebase,
  CodebaseArtifact,
  CodebaseArtifactsData,
  CodebaseBuild,
  CodebasesData,
  CodebaseSessionData,
  CodebaseSymbolsData,
  CodebaseVersion,
  CodebaseVersionsData,
  CreateCodebaseParams,
  CreateCodebaseSessionParams,
  DownloadCodebaseData,
  FileTreeData,
  ReadSourceData,
  ReadSourceParams,
  SSEEventHandler,
} from "./types";

const codebaseResourceClient = makeResourceClient<
  Codebase,
  CodebasesData,
  CodebaseVersion,
  CodebaseVersionsData,
  CodebaseBuild,
  CreateCodebaseParams,
  CreateCodebaseSessionParams,
  CodebaseSessionData
>("/codebases");

export const codebaseApi = {
  ...codebaseResourceClient,

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

  getVersionArtifacts: (
    codebaseId: string,
    versionId: string,
    kind?: string,
  ): Promise<CodebaseArtifactsData> => {
    return get<CodebaseArtifactsData>(
      `/codebases/${codebaseId}/versions/${versionId}/artifacts`,
      kind ? { kind } : undefined,
    );
  },

  readSource: (codebaseId: string, params: ReadSourceParams): Promise<ReadSourceData> => {
    return post<ReadSourceData>(`/codebases/${codebaseId}/source`, params);
  },

  readVersionSource: (
    codebaseId: string,
    versionId: string,
    params: ReadSourceParams,
  ): Promise<ReadSourceData> => {
    return post<ReadSourceData>(
      `/codebases/${codebaseId}/versions/${versionId}/source`,
      params,
    );
  },

  reanalyze: (codebaseId: string): Promise<Codebase> => {
    return post<Codebase>(`/codebases/${codebaseId}/reanalyze`);
  },

  download: (codebaseId: string): Promise<DownloadCodebaseData> => {
    return get<DownloadCodebaseData>(`/codebases/${codebaseId}/download`);
  },

  ingestStream: (
    codebaseId: string,
    onEvent: SSEEventHandler,
    onError?: (error: Error) => void,
    eventId?: string,
    onComplete?: () => void,
  ): (() => void) => {
    return createIngestStream(`/codebases/${codebaseId}/ingest`, onEvent, onError, eventId, onComplete);
  },
};

export type { Codebase, CodebaseArtifact };
