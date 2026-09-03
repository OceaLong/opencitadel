import { get, type RequestOptions } from "./fetch";
import type { components } from "./generated/schema";

export type ApprovalInboxItem = components["schemas"]["ApprovalInboxItem"];
export type ApprovalInboxResponse = components["schemas"]["ApprovalInboxResponse"];

/** GET /api/approvals 支持的 status 过滤值。 */
export type ApprovalInboxStatus = "pending" | "approved" | "rejected" | "cancelled" | "expired";

export const approvalsApi = {
  /**
   * 审批收件箱列表（跨会话）。status 缺省时返回全部状态。
   * 决策仍复用 sessionApi.decideApproval（POST /approval-batches/{id}/commands/decide）。
   */
  list: (
    params: { status?: ApprovalInboxStatus; limit?: number; offset?: number } = {},
    options?: RequestOptions,
  ): Promise<ApprovalInboxResponse> => {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    const encoded = query.toString();
    return get<ApprovalInboxResponse>(
      `/approvals${encoded ? `?${encoded}` : ""}`,
      undefined,
      options,
    );
  },
};
