import { get, post, type RequestOptions } from "./fetch";
import type { components } from "./generated/schema";

export type DefinitionInfo = components["schemas"]["DefinitionInfo"];
export type DefinitionPage = components["schemas"]["DefinitionPage"];
export type WorkerPage = components["schemas"]["WorkerPage"];
export type DefinitionRequestResult = components["schemas"]["DefinitionRequestResult"];
export const executionDefinitionsApi = {
  definitions: (offset = 0, options?: RequestOptions) =>
    get<DefinitionPage>("/admin/execution-definitions", { offset, limit: 25 }, options),
  workers: (offset = 0, options?: RequestOptions) =>
    get<WorkerPage>("/admin/execution-workers", { offset, limit: 25 }, options),
  head: (family: string, body: components["schemas"]["HeadChange"], options?: RequestOptions) =>
    post<DefinitionRequestResult>(
      `/admin/execution-definitions/${encodeURIComponent(family)}/head`,
      body,
      options,
    ),
  retire: (
    family: string,
    version: number,
    body: components["schemas"]["Retirement"],
    options?: RequestOptions,
  ) =>
    post<DefinitionRequestResult>(
      `/admin/execution-definitions/${encodeURIComponent(family)}/${version}/retire`,
      body,
      options,
    ),
  result: (id: string, options?: RequestOptions) =>
    get<DefinitionRequestResult>(
      `/admin/execution-definition-requests/${encodeURIComponent(id)}`,
      undefined,
      options,
    ),
};
