/** Evaluation contracts are generated from the authenticated API schemas. */
import type { components } from "../generated/schema";

export type DatasetSummary = components["schemas"]["DatasetSummary"];
export type DatasetDraft = components["schemas"]["DatasetDraft"];
export type DatasetVersion = components["schemas"]["DatasetVersion"];
export type CaseRevision = components["schemas"]["CaseRevision"];
export type ImportPreview = components["schemas"]["ImportPreview"];
export type ImportErrorItem = components["schemas"]["ImportErrorItem"];
export type CreateDatasetRequest = components["schemas"]["CreateDatasetRequest"];
export type UpdateCaseRequest = components["schemas"]["UpdateCaseRequest"];
export type ApplyImportRequest = components["schemas"]["ApplyImportRequest"];
export type FromRunRequest = components["schemas"]["FromRunRequest"];

export type ConfigSelection = components["schemas"]["ConfigSelection"];
export type ConfigVersion = components["schemas"]["PublicConfigVersion"];
export type RubricVersion = components["schemas"]["RubricVersion"];
export type SuiteVersion = components["schemas"]["PublicSuiteVersion"];
export type ConfigurationDraft = components["schemas"]["PublicConfigurationDraft"];
export type ConfigurationPage = components["schemas"]["ConfigurationPage"];
export type PreflightResult = components["schemas"]["PreflightResult"];
export type CreateConfigurationRequest = components["schemas"]["CreateConfigurationRequest"];
export type UpdateConfigurationRequest = components["schemas"]["UpdateConfigurationRequest"];
export type PublishConfigurationRequest = components["schemas"]["PublishConfigurationRequest"];
