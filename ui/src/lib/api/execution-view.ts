import {
  ApiError,
  createAuthenticatedEventStream,
  get,
  getBlob,
  parseSSEStream,
  type RequestOptions,
  snapshotRequestOptions,
} from "./fetch";
import type {
  ArtifactContentQuery,
  ArtifactProvenance,
  ContentPage,
  ContentQuery,
  EventsQuery,
  ExecutionReadErrorCode,
  ListRunsQuery,
  PublicEventPage,
  PublicExecutionEvent,
  RunViewPage,
  SourceContentQuery,
  StepDetail,
  StepQuery,
  StepsQuery,
  StepViewPage,
  TimelineQuery,
  TimelineView,
  ViewPage,
  ViewQuery,
} from "./types/execution-view";

const id = encodeURIComponent;
const runPath = (run: string) => `/execution-runs/${id(run)}`;
function params(query: object): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(query).filter(([, value]) => value != null));
}

/** Complete authorized text only. A failed/revoked continuation rejects the whole download. */
async function collectContent(
  read: (cursor: string | undefined, options: RequestOptions) => Promise<ContentPage>,
  options?: RequestOptions,
): Promise<Blob> {
  const scopedOptions = snapshotRequestOptions(options);
  const parts: string[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  let contentType = "text/plain";
  do {
    const page = await read(cursor, scopedOptions);
    if (page.availability !== "available" || page.content == null) {
      throw new ApiError(409, "resource_unavailable", {
        code: "resource_unavailable",
        reason: page.reason === "source_locator_unavailable" ? page.reason : undefined,
      });
    }
    contentType = page.content_type ?? "text/plain";
    parts.push(page.content);
    cursor = page.next_cursor ?? undefined;
    if (page.truncated !== Boolean(cursor) || (cursor && seen.has(cursor))) {
      throw new ApiError(409, "revision_conflict", { code: "revision_conflict" });
    }
    if (cursor) seen.add(cursor);
  } while (cursor);
  return new Blob(parts, { type: contentType });
}

export const executionViewApi = {
  listRuns: (query: ListRunsQuery = {}, options?: RequestOptions): Promise<RunViewPage> =>
    get("/execution-runs", params(query), options),
  getView: (run: string, query: ViewQuery = {}, options?: RequestOptions): Promise<ViewPage> =>
    get(`${runPath(run)}/view`, params(query), options),
  listSteps: (
    run: string,
    query: StepsQuery = {},
    options?: RequestOptions,
  ): Promise<StepViewPage> => get(`${runPath(run)}/steps`, params(query), options),
  getStep: (
    run: string,
    step: string,
    query: StepQuery = {},
    options?: RequestOptions,
  ): Promise<StepDetail> => get(`${runPath(run)}/steps/${id(step)}`, params(query), options),
  getTimeline: (
    run: string,
    query: TimelineQuery,
    options?: RequestOptions,
  ): Promise<TimelineView> => get(`${runPath(run)}/timeline`, params(query), options),
  getEvents: (
    run: string,
    query: EventsQuery = {},
    options?: RequestOptions,
  ): Promise<PublicEventPage> => get(`${runPath(run)}/events`, params(query), options),
  readContent: (
    run: string,
    step: string,
    query: ContentQuery,
    options?: RequestOptions,
  ): Promise<ContentPage> =>
    get(`${runPath(run)}/steps/${id(step)}/content`, params(query), options),
  getProvenance: (
    artifact: string,
    version: number,
    options?: RequestOptions,
    query?: { run_id: string; at: string },
  ): Promise<ArtifactProvenance[]> =>
    get(`/artifacts/${id(artifact)}/provenance`, { version, ...query }, options),
  readArtifact: (
    artifact: string,
    query: ArtifactContentQuery,
    options?: RequestOptions,
  ): Promise<ContentPage> =>
    get(`/execution-artifacts/${id(artifact)}/content`, params(query), options),
  readSource: (
    citation: string,
    query: SourceContentQuery = {},
    options?: RequestOptions,
  ): Promise<ContentPage> =>
    get(`/execution-sources/${id(citation)}/content`, params(query), options),
  downloadFileSource: (citation: string, options?: RequestOptions) =>
    getBlob(`/execution-sources/${id(citation)}/download`, options),
  downloadContent: (
    run: string,
    step: string,
    query: Omit<ContentQuery, "cursor">,
    options?: RequestOptions,
  ) =>
    collectContent(
      (cursor, scoped) => executionViewApi.readContent(run, step, { ...query, cursor }, scoped),
      options,
    ),
  downloadArtifact: (
    artifact: string,
    query: Omit<ArtifactContentQuery, "cursor">,
    options?: RequestOptions,
  ) =>
    collectContent(
      (cursor, scoped) => executionViewApi.readArtifact(artifact, { ...query, cursor }, scoped),
      options,
    ),
  downloadSource: (
    citation: string,
    query: Omit<SourceContentQuery, "cursor"> = {},
    options?: RequestOptions,
  ) =>
    collectContent(
      (cursor, scoped) => executionViewApi.readSource(citation, { ...query, cursor }, scoped),
      options,
    ),
  /** Reconnect with the last accepted event.cursor; refresh view on refresh or EOF. */
  streamEvents: async (
    run: string,
    onEvent: (event: PublicExecutionEvent) => void,
    options: RequestOptions & {
      lastEventId?: string;
      onRefresh?: (reason: { code: ExecutionReadErrorCode }) => void;
    } = {},
  ) => {
    const { lastEventId, onRefresh, ...request } = options;
    const stream = await createAuthenticatedEventStream(
      `${runPath(run)}/events/stream`,
      lastEventId,
      request,
    );
    let streamError: Error | undefined;
    await parseSSEStream(
      stream,
      (event) => {
        if (event.type === "refresh") onRefresh?.(event.data as { code: ExecutionReadErrorCode });
        else if (event.type === "execution") onEvent(event.data as PublicExecutionEvent);
      },
      (error) => {
        streamError = error;
      },
      { propagateAbort: true },
    );
    if (streamError) throw streamError;
  },
};
