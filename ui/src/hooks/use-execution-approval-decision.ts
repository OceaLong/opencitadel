"use client";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  type ApprovalTarget,
  decideCurrentApproval,
  type DecisionResult,
  findCurrentApproval,
} from "@/lib/execution-view/action-availability";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";
type Result = DecisionResult | { state: "checking" };
/** Current-run/source authority is supplied by the session's single workbench/cohort gate. */
export function useExecutionApprovalDecision({
  authority,
  runId,
  sessionId,
  onChanged,
}: {
  authority: string | null;
  runId: string;
  sessionId: string;
  onChanged: () => void;
}) {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const context = JSON.stringify([user?.id, scope?.workspaceId, scopeRevision, sessionId, runId]);
  const key =
    !loading && user && scope?.userId === user.id && authority
      ? JSON.stringify([user.id, scope.workspaceId, scopeRevision, sessionId, runId, authority])
      : null;
  const [snapshot, setSnapshot] = useState<{ key: string; results: Record<string, Result> } | null>(
    null,
  );
  const generation = useRef(0),
    controllers = useRef(new Set<AbortController>());
  // An uncertain write remains locked even when navigating away and back. Re-entry cannot retry it.
  const attempts = useRef(new Set<string>());
  const uncertain = useRef(new Map<string, { context: string; target: ApprovalTarget }>());
  const changed = useRef(onChanged);
  useLayoutEffect(() => {
    changed.current = onChanged;
  });
  useLayoutEffect(
    () => () => {
      generation.current++;
      for (const c of controllers.current) c.abort();
      controllers.current.clear();
    },
    [key],
  );
  const lifetime = useMemo(() => ({ key, active: false }), [key]);
  useLayoutEffect(() => {
    lifetime.active = true;
    return () => {
      lifetime.active = false;
    };
  }, [lifetime]);
  const workspaceId = scope?.workspaceId;
  // A fresh authorized workbench read can reconcile presentation, never unlock an uncertain write.
  useEffect(() => {
    if (!key || workspaceId == null) return;
    const controller = new AbortController(),
      captured = generation.current;
    const current = () => captured === generation.current && !controller.signal.aborted;
    const targets = [...uncertain.current.entries()].filter(
      ([, entry]) => entry.context === context,
    );
    void (async () => {
      for (const [attempt, entry] of targets) {
        try {
          const item = await findCurrentApproval(
            entry.target,
            { workspaceId, signal: controller.signal, skipErrorHandler: true },
            current,
          );
          if (!current()) return;
          if (item && item.status !== "pending") {
            uncertain.current.delete(attempt);
            setSnapshot((previous) =>
              previous?.key === context &&
              ["unknown", "checking"].includes(
                previous.results[entry.target.approvalId]?.state ?? "",
              )
                ? {
                    ...previous,
                    results: {
                      ...previous.results,
                      [entry.target.approvalId]: { state: "settled", item },
                    },
                  }
                : previous,
            );
          }
        } catch {
          if (!current()) return;
        }
      }
    })();
    return () => controller.abort();
  }, [key, context, workspaceId]);
  const decide = async (
    approvalId: string,
    decision: "approved" | "rejected",
    feedback = "",
    expectedSubjectActivityId?: string | null,
  ) => {
    if (!key || !scope || !lifetime.active) return;
    const captured = generation.current;
    const attempt = JSON.stringify([user?.id, scope.workspaceId, sessionId, runId, approvalId]);
    if (attempts.current.has(attempt)) return;
    attempts.current.add(attempt);
    const controller = new AbortController();
    controllers.current.add(controller);
    const current = () => captured === generation.current && !controller.signal.aborted;
    const publish = (result: Result) => {
      if (current())
        setSnapshot((previous) => ({
          key: context,
          results: { ...(previous?.key === context ? previous.results : {}), [approvalId]: result },
        }));
    };
    const target = { approvalId, runId, sessionId, expectedSubjectActivityId };
    let submitted = false;
    publish({ state: "checking" });
    try {
      const result = await decideCurrentApproval({
        target,
        decision,
        feedback,
        isCurrent: current,
        onSubmit: () => {
          submitted = true;
          uncertain.current.set(attempt, { context, target });
        },
        options: {
          workspaceId: scope.workspaceId,
          signal: controller.signal,
          skipErrorHandler: true,
        },
      });
      if (!current()) return;
      publish(result);
      if (result.state === "settled") uncertain.current.delete(attempt);
      if (result.state === "unavailable") attempts.current.delete(attempt);
      changed.current();
    } catch {
      if (current()) {
        publish({ state: "unavailable" });
        attempts.current.delete(attempt);
        changed.current();
      }
    } finally {
      controllers.current.delete(controller);
      if (!submitted) attempts.current.delete(attempt);
      if (!current())
        setSnapshot((previous) =>
          previous?.key === context && previous.results[approvalId]?.state === "checking"
            ? {
                ...previous,
                results: {
                  ...previous.results,
                  [approvalId]: { state: submitted ? "unknown" : "unavailable" },
                },
              }
            : previous,
        );
    }
  };
  return { results: key && snapshot?.key === context ? snapshot.results : {}, decide };
}
