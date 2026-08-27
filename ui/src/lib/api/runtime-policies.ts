import { get, post } from "./fetch";
import type { components } from "./generated/schema";

export type ExecutionPolicy = components["schemas"]["ExecutionPolicy-Output"];
export type ExecutionPolicyInput = components["schemas"]["ExecutionPolicy-Input"];
export type OperationsPolicy = components["schemas"]["OperationsPolicy-Output"];
export type OperationsPolicyInput = components["schemas"]["OperationsPolicy-Input"];
export type ActiveExecutionPolicy = components["schemas"]["ActiveExecutionPolicyResponse"];
export type ActiveOperationsPolicy = components["schemas"]["ActiveOperationsPolicyResponse"];
export type RuntimePolicyHead = components["schemas"]["RuntimePolicyHeadResponse"];
export type ExecutionPolicyRevision = components["schemas"]["ExecutionPolicyRevisionResponse"];
export type OperationsPolicyRevision = components["schemas"]["OperationsPolicyRevisionResponse"];
export type ExecutionPolicyRevisionPage =
  components["schemas"]["ExecutionPolicyRevisionListResponse"];
export type OperationsPolicyRevisionPage =
  components["schemas"]["OperationsPolicyRevisionListResponse"];
export type CreateExecutionPolicyRevisionRequest =
  components["schemas"]["CreateExecutionPolicyRevisionRequest"];
export type CreateOperationsPolicyRevisionRequest =
  components["schemas"]["CreateOperationsPolicyRevisionRequest"];
export type RestorePolicyRevisionRequest = components["schemas"]["RestorePolicyRevisionRequest"];

export const runtimePolicyApi = {
  getExecution: (): Promise<ActiveExecutionPolicy> =>
    get<ActiveExecutionPolicy>("/runtime-policies/execution"),
  getOperations: (): Promise<ActiveOperationsPolicy> =>
    get<ActiveOperationsPolicy>("/runtime-policies/operations"),
  listExecutionRevisions: (limit = 20, offset = 0): Promise<ExecutionPolicyRevisionPage> =>
    get<ExecutionPolicyRevisionPage>("/runtime-policies/execution/revisions", { limit, offset }),
  listOperationsRevisions: (limit = 20, offset = 0): Promise<OperationsPolicyRevisionPage> =>
    get<OperationsPolicyRevisionPage>("/runtime-policies/operations/revisions", {
      limit,
      offset,
    }),
  createExecution: (body: CreateExecutionPolicyRevisionRequest): Promise<ActiveExecutionPolicy> =>
    post<ActiveExecutionPolicy>("/runtime-policies/execution/revisions", body),
  createOperations: (
    body: CreateOperationsPolicyRevisionRequest,
  ): Promise<ActiveOperationsPolicy> =>
    post<ActiveOperationsPolicy>("/runtime-policies/operations/revisions", body),
  restoreExecution: (
    revisionId: string,
    body: RestorePolicyRevisionRequest,
  ): Promise<ActiveExecutionPolicy> =>
    post<ActiveExecutionPolicy>(
      `/runtime-policies/execution/revisions/${encodeURIComponent(revisionId)}/restore`,
      body,
    ),
  restoreOperations: (
    revisionId: string,
    body: RestorePolicyRevisionRequest,
  ): Promise<ActiveOperationsPolicy> =>
    post<ActiveOperationsPolicy>(
      `/runtime-policies/operations/revisions/${encodeURIComponent(revisionId)}/restore`,
      body,
    ),
};
