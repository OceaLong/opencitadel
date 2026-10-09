import {
  createAuthenticatedEventStream,
  get,
  parseSSEStream,
  patch,
  post,
  type RequestOptions,
} from "./fetch";
import type { components } from "./generated/schema";
import type {
  ApplyImportRequest,
  ConfigurationDraft,
  ConfigurationPage,
  CreateConfigurationRequest,
  CreateDatasetRequest,
  DatasetDraft,
  DatasetSummary,
  DatasetVersion,
  FromRunRequest,
  ImportPreview,
  PreflightResult,
  PublishConfigurationRequest,
  UpdateCaseRequest,
  UpdateConfigurationRequest,
} from "./types/evaluations";

type S = components["schemas"];
export type Collection = "configs" | "rubrics" | "suites";
const segment = encodeURIComponent;
function query(values: Record<string, string | undefined>) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== undefined) q.set(key, value);
  return q.size ? `?${q}` : "";
}
export const evaluationApi = {
  builtinTools: (mode: "ask" | "agent", skillId: string | undefined, o: RequestOptions) =>
    get<S["BuiltinToolChoice"][]>(
      `/evaluation/config-options/tools${query({ mode, skill_id: skillId })}`,
      undefined,
      o,
    ),
  datasets: (o: RequestOptions) => get<DatasetSummary[]>("/evaluation/datasets", undefined, o),
  createDataset: (body: CreateDatasetRequest, o: RequestOptions) =>
    post<DatasetDraft>("/evaluation/datasets", body, o),
  dataset: (id: string, o: RequestOptions) =>
    get<DatasetDraft>(`/evaluation/datasets/${segment(id)}`, undefined, o),
  updateCase: (id: string, key: string, body: UpdateCaseRequest, o: RequestOptions) =>
    patch<DatasetDraft>(`/evaluation/datasets/${segment(id)}/cases/${segment(key)}`, body, o),
  validateImport: (
    id: string,
    file: File,
    requestId: string,
    revision: number,
    o: RequestOptions,
  ) => {
    const body = new FormData();
    body.set("file", file);
    body.set("request_id", requestId);
    body.set("expected_revision", String(revision));
    return post<ImportPreview>(`/evaluation/datasets/${segment(id)}/imports/validate`, body, o);
  },
  applyImport: (id: string, importId: string, body: ApplyImportRequest, o: RequestOptions) =>
    post<DatasetDraft>(
      `/evaluation/datasets/${segment(id)}/imports/${segment(importId)}/apply`,
      body,
      o,
    ),
  publishDataset: (id: string, body: S["MutationRequest"], o: RequestOptions) =>
    post<DatasetVersion>(`/evaluation/datasets/${segment(id)}/publish`, body, o),
  datasetVersions: (id: string, o: RequestOptions, cursor?: string) =>
    get<S["DatasetVersionPage"]>(
      `/evaluation/datasets/${segment(id)}/versions${query({ cursor })}`,
      undefined,
      o,
    ),
  datasetVersion: (id: string, o: RequestOptions) =>
    get<DatasetVersion>(`/evaluation/dataset-versions/${segment(id)}`, undefined, o),
  previewFromRun: (id: string, body: S["FromRunPreviewRequest"], o: RequestOptions) =>
    post<S["CaseRevision"]>(`/evaluation/datasets/${segment(id)}/from-run/preview`, body, o),
  fromRun: (id: string, body: FromRunRequest, o: RequestOptions) =>
    post<DatasetDraft>(`/evaluation/datasets/${segment(id)}/from-run`, body, o),
  list: (
    kind: Collection,
    o: RequestOptions,
    versions = false,
    cursor?: string,
    entityId?: string,
  ) =>
    get<ConfigurationPage>(
      `/evaluation/${kind}${versions ? "/versions" : ""}${query({ cursor, entity_id: entityId })}`,
      undefined,
      o,
    ),
  draft: (kind: Collection, id: string, o: RequestOptions) =>
    get<ConfigurationDraft>(`/evaluation/${kind}/${segment(id)}`, undefined, o),
  create: (kind: Collection, body: CreateConfigurationRequest, o: RequestOptions) =>
    post<ConfigurationDraft>(`/evaluation/${kind}`, body, o),
  update: (kind: Collection, id: string, body: UpdateConfigurationRequest, o: RequestOptions) =>
    patch<ConfigurationDraft>(`/evaluation/${kind}/${segment(id)}`, body, o),
  publish: (kind: Collection, id: string, body: PublishConfigurationRequest, o: RequestOptions) =>
    post<S["PublicConfigVersion"] | S["RubricVersion"] | S["PublicSuiteVersion"]>(
      `/evaluation/${kind}/${segment(id)}/publish`,
      body,
      o,
    ),
  version: (kind: Collection, id: string, o: RequestOptions) =>
    get<S["PublicConfigVersion"] | S["RubricVersion"] | S["PublicSuiteVersion"]>(
      `/evaluation/${kind}/versions/${segment(id)}`,
      undefined,
      o,
    ),
  preflight: (suiteVersion: string, o: RequestOptions) =>
    post<PreflightResult>("/evaluation/batches/preflight", { suite_version: suiteVersion }, o),
  start: (body: S["StartBatchRequest"], o: RequestOptions) =>
    post<S["BatchView"]>("/evaluation/batches", body, o),
  batches: (o: RequestOptions, cursor?: string) =>
    get<S["BatchListPage"]>(`/evaluation/batches${query({ cursor })}`, undefined, o),
  summary: (
    id: string,
    o: RequestOptions,
    values: {
      source?: string;
      dimension?: string;
      rubric_id?: string;
      result_id?: string;
      evaluation_revision?: string;
      cursor?: string;
    } = {},
  ) =>
    get<S["SummaryPage"]>(
      `/evaluation/batches/${segment(id)}/summary${query(values)}`,
      undefined,
      o,
    ),
  batchEvents: async (
    id: string,
    o: RequestOptions,
    changed: () => void,
    refresh: (code: string) => void,
    cursor?: string,
  ) => {
    const stream = await createAuthenticatedEventStream(
      `/evaluation/batches/${segment(id)}/events/stream`,
      cursor,
      o,
    );
    let failure: Error | undefined;
    await parseSSEStream(
      stream,
      (event) => {
        if (event.type === "evaluation") changed();
        if (event.type === "refresh") refresh((event.data as { code: string }).code);
      },
      (error) => {
        failure = error;
      },
      { propagateAbort: true },
    );
    if (failure) throw failure;
  },
  batch: (id: string, o: RequestOptions) =>
    get<S["BatchView"]>(`/evaluation/batches/${segment(id)}`, undefined, o),
  batchEnvironments: (id: string, o: RequestOptions, cursor?: string) =>
    get<S["BatchEnvironmentPage"]>(
      `/evaluation/batches/${segment(id)}/environments${query({ cursor })}`,
      undefined,
      o,
    ),
  batchResults: (id: string, o: RequestOptions, cursor?: string) =>
    get<S["ResultPage"]>(
      `/evaluation/batches/${segment(id)}/results${query({ cursor })}`,
      undefined,
      o,
    ),
  cancelBatch: (id: string, body: S["BatchCommandRequest"], o: RequestOptions) =>
    post<S["BatchView"]>(`/evaluation/batches/${segment(id)}/commands/cancel`, body, o),
  retryBatch: (id: string, body: S["BatchCommandRequest"], o: RequestOptions) =>
    post<S["BatchView"]>(`/evaluation/batches/${segment(id)}/commands/retry-failed`, body, o),
  reviews: (o: RequestOptions, cursor?: string) =>
    get<S["ReviewPage"]>(`/evaluation/reviews${query({ cursor })}`, undefined, o),
  reviewContext: (id: string, o: RequestOptions) =>
    get<S["CurrentReviewContext"]>(
      `/evaluation/results/${segment(id)}/review-context`,
      undefined,
      o,
    ),
  scores: (id: string, o: RequestOptions, revision?: number, cursor?: string) =>
    get<S["ScoreHistoryPage"]>(
      `/evaluation/results/${segment(id)}/scores${query({ evaluation_revision: revision?.toString(), cursor })}`,
      undefined,
      o,
    ),
  review: (id: string, body: S["AppendHumanReview"], o: RequestOptions) =>
    post<S["ReviewReceipt"]>(`/evaluation/results/${segment(id)}/scores`, body, o),
  rescore: (id: string, body: S["RescoreCommand"], o: RequestOptions) =>
    post<S["ReviewReceipt"]>(`/evaluation/results/${segment(id)}/commands/rescore`, body, o),
  cancelJudge: (id: string, body: S["CancelJudgeCommand"], o: RequestOptions) =>
    post<S["ReviewReceipt"]>(`/evaluation/results/${segment(id)}/commands/cancel-judge`, body, o),
  reviewCommand: (id: string, o: RequestOptions) =>
    get<S["ReviewReceipt"]>(`/evaluation/reviews/commands/${segment(id)}`, undefined, o),
  recordings: (o: RequestOptions, cursor?: string) =>
    get<S["RecordingPage"]>(`/evaluation/recordings${query({ cursor })}`, undefined, o),
  recordingCandidates: (run: string, o: RequestOptions, cursor?: string) =>
    get<S["RecordingCandidatePage"]>(
      `/evaluation/recordings/sources/${segment(run)}${query({ cursor })}`,
      undefined,
      o,
    ),
  environments: (o: RequestOptions, cursor?: string) =>
    get<S["EnvironmentPage"]>(`/evaluation/environments${query({ cursor })}`, undefined, o),
  environmentInventory: (o: RequestOptions) =>
    get<S["EnvironmentInventory"]>("/evaluation/environments/inventory", undefined, o),
  registerInventory: (
    kind: "target" | "credential",
    id: string,
    body: S["InventorySelectionRequest"],
    o: RequestOptions,
  ) =>
    post<S["RegistryReference"]>(
      `/evaluation/environments/inventory/${kind}/${segment(id)}/register`,
      body,
      o,
    ),
  registerEnvironment: (body: S["RegisterEnvironmentRequest"], o: RequestOptions) =>
    post<S["RegistryReference"]>("/evaluation/environments/registry", body, o),
  createRecording: (body: S["CreateRecordingRequest"], o: RequestOptions) =>
    post<S["RecordingJob"]>("/evaluation/recordings", body, o),
  recording: (id: string, o: RequestOptions) =>
    get<S["RecordingJob"]>(`/evaluation/recordings/${segment(id)}`, undefined, o),
  recordingResult: (id: string, o: RequestOptions) =>
    get<S["RecordingResult"]>(`/evaluation/recordings/${segment(id)}/result`, undefined, o),
};
