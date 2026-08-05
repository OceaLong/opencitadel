import { authenticatedFetch, del, get, patch, post } from "./fetch";
import type {
  PatrolFinding,
  PatrolPack,
  PatrolPackConfig,
  PatrolPackList,
  PatrolPackMetrics,
  PatrolRemediation,
  PatrolRemediationAction,
  PatrolRemediationList,
  PatrolRun,
  PatrolRunDetail,
  PatrolRunList,
} from "./types";

export type ProposePatrolRemediation = {
  action: PatrolRemediationAction;
  params?: Record<string, unknown>;
  workload?: string;
};

export type CreatePatrolPack = {
  name: string;
  mcp_server_id: string;
  template_id?: "kubernetes-baseline-v1";
  config?: Partial<PatrolPackConfig>;
};

function query(params: Record<string, string | number | undefined>) {
  const value = new URLSearchParams();
  Object.entries(params).forEach(
    ([key, item]) => item !== undefined && value.set(key, String(item)),
  );
  const encoded = value.toString();
  return encoded ? `?${encoded}` : "";
}

export const patrolsApi = {
  listPacks: (params: { limit?: number; offset?: number } = {}): Promise<PatrolPackList> =>
    get<PatrolPackList>(`/patrol-packs${query(params)}`),
  createPack: (params: CreatePatrolPack): Promise<PatrolPack> =>
    post<PatrolPack>("/patrol-packs", params),
  getPack: (id: string): Promise<PatrolPack> => get<PatrolPack>(`/patrol-packs/${id}`),
  getPackMetrics: (id: string): Promise<PatrolPackMetrics> =>
    get<PatrolPackMetrics>(`/patrol-packs/${id}/metrics`),
  updatePack: (
    id: string,
    params: { version: number; name?: string; mcp_server_id?: string; config?: PatrolPackConfig },
  ): Promise<PatrolPack> => patch<PatrolPack>(`/patrol-packs/${id}`, params),
  deletePack: (id: string): Promise<{ deleted: boolean }> => del(`/patrol-packs/${id}`),
  validatePack: (id: string): Promise<PatrolPack> => post(`/patrol-packs/${id}/validate`, {}),
  activatePack: (id: string): Promise<PatrolPack> => post(`/patrol-packs/${id}/activate`, {}),
  pausePack: (id: string): Promise<PatrolPack> => post(`/patrol-packs/${id}/pause`, {}),
  triggerPack: (id: string, idempotencyKey: string): Promise<PatrolRun> =>
    post(`/patrol-packs/${id}/trigger`, {}, { headers: { "Idempotency-Key": idempotencyKey } }),
  listRuns: (
    params: {
      pack_id?: string;
      status?: string;
      created_from?: string;
      created_to?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<PatrolRunList> => get<PatrolRunList>(`/patrol-runs${query(params)}`),
  getRun: (id: string): Promise<PatrolRunDetail> => get(`/patrol-runs/${id}`),
  cancelRun: (id: string): Promise<PatrolRun> => post(`/patrol-runs/${id}/cancel`, {}),
  replayRun: (id: string): Promise<PatrolRun> => post(`/patrol-runs/${id}/replay`, {}),
  decideFinding: (
    id: string,
    action: "acknowledge" | "resolve" | "false-positive",
    reason: string,
  ): Promise<PatrolFinding> => post(`/patrol-findings/${id}/${action}`, { reason }),
  proposeRemediation: (
    findingId: string,
    params: ProposePatrolRemediation,
  ): Promise<PatrolRemediation> => post(`/patrol-findings/${findingId}/remediations`, params),
  listRemediations: (runId: string): Promise<PatrolRemediationList> =>
    get<PatrolRemediationList>(`/patrol-runs/${runId}/remediations`),
  downloadEvidence: async (id: string): Promise<Blob> => {
    const response = await authenticatedFetch(`/patrol-runs/${id}/evidence`);
    if (!response.ok) throw new Error(`Evidence download failed (${response.status})`);
    return response.blob();
  },
};
