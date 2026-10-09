import { get, getBlob, post, put, type RequestOptions } from "./fetch";
import type {
  AnalysisPreference,
  AnalysisPreferenceUpdate,
  AnalysisRunPage,
  AnalysisSummary,
  ComparisonAlignment,
  ComparisonArtifactDiff,
  ComparisonCreate,
  ComparisonDiffPage,
  ComparisonDiffQueued,
  ComparisonEnvelope,
  ComparisonQuery,
  ComparisonRefresh,
  ExportCreate,
  ExportJob,
  SummaryQuery,
} from "./types/execution-analysis";
import type { ContentPage } from "./types/execution-view";

function params(query: object): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(query).filter(([, value]) => value != null));
}

function readComparison(
  id: string,
  query: ComparisonQuery,
  options?: RequestOptions,
): Promise<ComparisonEnvelope> {
  const { detail_run_ids, ...scalars } = query;
  if (!detail_run_ids?.length) return get(comparisonPath(id), params(scalars), options);
  const search = new URLSearchParams();
  Object.entries(params(scalars)).forEach(([key, value]) => search.append(key, String(value)));
  detail_run_ids.forEach((run) => search.append("detail_run_ids", run));
  return get(`${comparisonPath(id)}?${search}`, undefined, options);
}

const comparisonPath = (id: string) => `/execution-comparisons/${encodeURIComponent(id)}`;

export const analysisApi = {
  body: (
    id: string,
    query: {
      revision: number;
      run_id: string;
      step_id: string;
      kind: "input" | "output" | "artifact";
      artifact_id?: string;
      version?: number;
      cursor?: string;
    },
    options?: RequestOptions,
  ): Promise<ContentPage> => get(`${comparisonPath(id)}/body`, params(query), options),
  runs: (
    query: SummaryQuery & { watermark: string; cursor?: string; limit?: number },
    options?: RequestOptions,
  ): Promise<AnalysisRunPage> =>
    get(
      "/execution-analysis/runs",
      params({ ...query, filters: JSON.stringify(query.filters ?? {}) }),
      options,
    ),
  createExport: (payload: ExportCreate, options?: RequestOptions): Promise<ExportJob> =>
    post("/execution-analysis/exports", payload, options),
  getExport: (id: string, options?: RequestOptions): Promise<ExportJob> =>
    get(`/execution-analysis/exports/${encodeURIComponent(id)}`, undefined, options),
  downloadExport: (id: string, options?: RequestOptions): Promise<Blob> =>
    getBlob(`/execution-analysis/exports/${encodeURIComponent(id)}/content`, options),
  summary: (query: SummaryQuery = {}, options?: RequestOptions): Promise<AnalysisSummary> =>
    get(
      "/execution-analysis/summary",
      params({
        ...query,
        filters: query.filters ? JSON.stringify(query.filters) : undefined,
      }),
      options,
    ),
  createComparison: (
    payload: ComparisonCreate,
    options?: RequestOptions,
  ): Promise<ComparisonEnvelope> => post("/execution-comparisons", payload, options),
  getComparison: (
    id: string,
    query: ComparisonQuery,
    options?: RequestOptions,
  ): Promise<ComparisonEnvelope> => readComparison(id, query, options),
  refreshComparison: (
    id: string,
    payload: ComparisonRefresh,
    options?: RequestOptions,
  ): Promise<ComparisonEnvelope> => post(`${comparisonPath(id)}/refresh`, payload, options),
  align: (
    id: string,
    payload: ComparisonAlignment,
    options?: RequestOptions,
  ): Promise<ComparisonEnvelope> => post(`${comparisonPath(id)}/alignments`, payload, options),
  createArtifactDiff: (
    id: string,
    payload: ComparisonArtifactDiff,
    options?: RequestOptions,
  ): Promise<ComparisonDiffQueued> =>
    post(`${comparisonPath(id)}/artifact-diffs`, payload, options),
  getArtifactDiff: (
    id: string,
    cursor?: string,
    options?: RequestOptions,
  ): Promise<ComparisonDiffPage> =>
    get(`/execution-analysis/diff-jobs/${encodeURIComponent(id)}`, params({ cursor }), options),
  getPreferences: (options?: RequestOptions): Promise<AnalysisPreference> =>
    get("/execution-analysis/preferences", undefined, options),
  updatePreferences: (
    payload: AnalysisPreferenceUpdate,
    options?: RequestOptions,
  ): Promise<AnalysisPreference> => put("/execution-analysis/preferences", payload, options),
};
