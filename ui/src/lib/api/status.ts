import { get } from "./fetch";
import type { components } from "./generated/schema";

/** 单个组件的健康检查结果（postgres / redis / fastapi 等）。 */
export type SystemHealthStatus = components["schemas"]["HealthStatus"];

export const statusApi = {
  /** GET /api/status：系统组件健康检查列表。 */
  get: (): Promise<SystemHealthStatus[]> => get<SystemHealthStatus[]>("/status"),
};
