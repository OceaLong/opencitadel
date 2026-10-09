import { type ApprovalInboxItem, approvalsApi } from "@/lib/api/approvals";
import { type RequestOptions, snapshotRequestOptions } from "@/lib/api/fetch";
import { sessionApi } from "@/lib/api/session";
export type ApprovalTarget = {
  approvalId: string;
  runId: string;
  sessionId: string;
  expectedSubjectActivityId?: string | null;
};
export type DecisionResult =
  | { state: "stale" | "unavailable" | "unknown" }
  | { state: "settled"; item: ApprovalInboxItem };
export function canDecideApproval(v: {
  at: string | null;
  status: string;
  allowed: boolean;
}): boolean {
  return v.at === null && v.status === "pending" && v.allowed;
}
/** The inbox is paged; a missing first-page row is never evidence of a decision. */
export async function findCurrentApproval(
  target: ApprovalTarget,
  options: RequestOptions,
  isCurrent: () => boolean,
): Promise<ApprovalInboxItem | null> {
  for (let offset = 0; isCurrent(); offset += 200) {
    const page = await approvalsApi.list({ limit: 200, offset }, options);
    if (!isCurrent()) return null;
    const match = page.items.find(
      (item) =>
        item.approval_id === target.approvalId &&
        item.run_id === target.runId &&
        item.source_entity_type === "session" &&
        item.source_entity_id === target.sessionId,
    );
    if (match)
      return target.expectedSubjectActivityId != null &&
        match.subject_activity_id !== target.expectedSubjectActivityId
        ? null
        : match;
    if (page.items.length < 200) return null;
  }
  return null;
}
export async function decideCurrentApproval(v: {
  target: ApprovalTarget;
  decision: "approved" | "rejected";
  feedback?: string;
  isCurrent: () => boolean;
  onSubmit?: () => void;
  options?: RequestOptions;
}): Promise<DecisionResult> {
  const options = snapshotRequestOptions(v.options);
  if (!v.isCurrent()) return { state: "stale" };
  const pending = await findCurrentApproval(v.target, options, v.isCurrent);
  if (!v.isCurrent()) return { state: "stale" };
  if (!pending) return { state: "unavailable" };
  if (pending.status !== "pending") return { state: "settled", item: pending };
  // No optimistic success: the current server can return HTTP 200 for a losing race.
  v.onSubmit?.();
  try {
    await sessionApi.decideApproval(v.target.approvalId, v.decision, v.feedback ?? "", options);
  } catch {
    /* Reconcile every uncertain response, including 404/409. Never retry a write. */
  }
  if (!v.isCurrent()) return { state: "stale" };
  try {
    const persisted = await findCurrentApproval(v.target, options, v.isCurrent);
    if (!v.isCurrent()) return { state: "stale" };
    return persisted && persisted.status !== "pending"
      ? { state: "settled", item: persisted }
      : { state: "unknown" };
  } catch {
    return { state: v.isCurrent() ? "unknown" : "stale" };
  }
}
