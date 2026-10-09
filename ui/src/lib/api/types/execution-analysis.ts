import type { components, paths } from "../generated/schema";

export type AnalysisSummary = components["schemas"]["AnalysisSummary"];
export type AnalysisPreference = components["schemas"]["AnalysisPreference"];
export type AnalysisPreferenceUpdate = components["schemas"]["AnalysisPreferenceUpdate"];
export type ComparisonCreate = components["schemas"]["ComparisonCreate"];
export type ComparisonRefresh = components["schemas"]["ComparisonRefresh"];
export type ComparisonAlignment = components["schemas"]["ComparisonAlignment"];
export type ComparisonArtifactDiff = components["schemas"]["ComparisonArtifactDiff"];
export type ComparisonEnvelope = components["schemas"]["ComparisonEnvelope"];
export type ComparisonDiffQueued = components["schemas"]["ComparisonDiffQueued"];
export type ComparisonDiffPage = components["schemas"]["ComparisonDiffPage"];
export type ComparisonQuery = NonNullable<
  paths["/api/execution-comparisons/{comparison_id}"]["get"]["parameters"]["query"]
>;
export type AnalysisFilters = Partial<
  Record<
    | "family"
    | "session"
    | "model_revision"
    | "configuration_revision"
    | "mode"
    | "tool"
    | "status"
    | "purpose"
    | "batch_id"
    | "start"
    | "end",
    string
  >
> & {
  accounting?: "run" | "selected_result" | "batch_total";
  comparison_config_version_ids?: string[];
};
export type SummaryQuery = {
  filters?: AnalysisFilters;
  grain?: "hour" | "day";
  timezone?: string;
  watermark?: string;
};

export type ExportJob = components["schemas"]["ExportJob"];
export type ExportCreate =
  | components["schemas"]["FilterExport"]
  | components["schemas"]["ComparisonExport"]
  | components["schemas"]["BatchExport"];

export type AnalysisRunPage = components["schemas"]["AnalysisRunPage"];
export type AnalysisRun = components["schemas"]["AnalysisRun"];
