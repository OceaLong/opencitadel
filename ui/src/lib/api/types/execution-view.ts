import type { components, paths } from "../generated/schema";

export type RunView = components["schemas"]["RunView"];
export type ViewPage = components["schemas"]["ViewPage"];
export type RunViewPage = components["schemas"]["RunViewPage"];
export type StepView = components["schemas"]["StepView"];
export type StepDetail = components["schemas"]["StepDetailResponse"];
export type StepViewPage = components["schemas"]["StepViewPage"];
export type TimelineView = components["schemas"]["TimelineView"];
export type PublicEventPage = components["schemas"]["PublicEventPage"];
export type PublicExecutionEvent = components["schemas"]["PublicExecutionEvent"];
export type ContentPage = components["schemas"]["ContentPage"];
export type ArtifactProvenance = components["schemas"]["ArtifactProvenanceResponse"];
export type ExecutionReadErrorCode = components["schemas"]["ExecutionReadError"]["code"];
export type ListRunsQuery = NonNullable<paths["/api/execution-runs"]["get"]["parameters"]["query"]>;
export type ViewQuery = NonNullable<
  paths["/api/execution-runs/{run_id}/view"]["get"]["parameters"]["query"]
>;
export type StepsQuery = NonNullable<
  paths["/api/execution-runs/{run_id}/steps"]["get"]["parameters"]["query"]
>;
export type StepQuery = NonNullable<
  paths["/api/execution-runs/{run_id}/steps/{step_id}"]["get"]["parameters"]["query"]
>;
export type TimelineQuery = NonNullable<
  paths["/api/execution-runs/{run_id}/timeline"]["get"]["parameters"]["query"]
>;
export type EventsQuery = NonNullable<
  paths["/api/execution-runs/{run_id}/events"]["get"]["parameters"]["query"]
>;
export type ContentQuery = NonNullable<
  paths["/api/execution-runs/{run_id}/steps/{step_id}/content"]["get"]["parameters"]["query"]
>;
export type ArtifactContentQuery = NonNullable<
  paths["/api/execution-artifacts/{artifact_id}/content"]["get"]["parameters"]["query"]
>;
export type SourceContentQuery = NonNullable<
  paths["/api/execution-sources/{citation_id}/content"]["get"]["parameters"]["query"]
>;
