"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { ApprovalActionsBar } from "@/components/session/approval-actions-bar";
import { ClarificationCard } from "@/components/session/clarification-card";

import type { ApprovalInboxItem } from "@/lib/api/approvals";
import { ApiError } from "@/lib/api/fetch";
import type { PendingAskEventData } from "@/lib/api/types";
import type { ViewPage } from "@/lib/api/types/execution-view";
import {
  canDecideApproval,
  type DecisionResult,
  findCurrentApproval,
} from "@/lib/execution-view/action-availability";
export type ApprovalActionContext = {
  sessionId: string;
  allowed: boolean;
  decide: (
    id: string,
    decision: "approved" | "rejected",
    feedback?: string,
    expectedSubjectActivityId?: string | null,
  ) => Promise<void>;
  results: Record<string, DecisionResult | { state: "checking" }>;
  ask: PendingAskEventData | null;
};
export type ApprovalDetailProps = {
  approval: NonNullable<ViewPage["approvals"]>[number];
  runId: string;
  workspaceId: string;
  context?: ApprovalActionContext;
  onRevoked: () => void;
};
export function ApprovalDetail(props: ApprovalDetailProps) {
  const identity = JSON.stringify([
    props.workspaceId,
    props.runId,
    props.approval.approval_id,
    props.approval.subject_activity_id,
    props.context?.sessionId,
    props.context?.allowed,
  ]);
  return <ApprovalEvidence key={identity} {...props} />;
}
function ApprovalEvidence({
  approval,
  runId,
  workspaceId,
  context,
  onRevoked,
}: ApprovalDetailProps) {
  const t = useTranslations("executionDetail");
  const [item, setItem] = useState<ApprovalInboxItem | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const revoked = useRef(onRevoked);
  useLayoutEffect(() => {
    revoked.current = onRevoked;
  });
  const allowed = context?.allowed ?? false,
    sessionId = context?.sessionId;
  useLayoutEffect(() => {
    if (!allowed || !sessionId) return;
    const controller = new AbortController();
    void findCurrentApproval(
      {
        approvalId: approval.approval_id,
        runId,
        sessionId,
        expectedSubjectActivityId: approval.subject_activity_id,
      },
      { workspaceId, signal: controller.signal, skipErrorHandler: true },
      () => !controller.signal.aborted,
    )
      .then((value) => {
        if (controller.signal.aborted) return;
        if (value?.subject_activity_id !== approval.subject_activity_id) {
          setUnavailable(true);
          return;
        }
        setItem(value);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && [401, 403, 404].includes(error.code)) revoked.current();
        else setUnavailable(true);
      });
    return () => controller.abort();
  }, [allowed, sessionId, approval.approval_id, approval.subject_activity_id, runId, workspaceId]);
  const result = context?.results[approval.approval_id];
  const status =
    result?.state === "settled"
      ? (result.item.decision ?? result.item.status)
      : (approval.decision ?? approval.status ?? t("notRecorded"));
  const enabled = canDecideApproval({
    at: null,
    status: item?.status ?? "unknown",
    allowed: allowed && !unavailable && (!result || result.state === "unavailable"),
  });
  const currentAsk = context?.ask;
  const ask =
    currentAsk?.ask_id === approval.approval_id && currentAsk?.run_id === runId ? currentAsk : null;
  return (
    <section className="space-y-2 border-b pb-3">
      <h3 className="font-mono text-xs break-all">{approval.approval_id}</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt>{t("operation")}</dt>
        <dd>{approval.approval_kind}</dd>
        <dt>{t("subject")}</dt>
        <dd className="break-all">{approval.subject_activity_id ?? t("notRecorded")}</dd>
      </dl>
      <p>{t("recordedDecision", { status })}</p>
      {item && (
        <>
          <p>{item.subject_label}</p>
          <p className="text-muted-foreground">{item.risk_summary}</p>
        </>
      )}
      {unavailable && <p role="status">{t("unavailable")}</p>}
      {result && <DecisionNotice result={result} />}
      {context &&
        item &&
        (item.approval_kind === "clarification" || ask ? (
          ask && (
            <ClarificationCard
              question={ask.question}
              choices={ask.choices}
              disabled={!enabled}
              onChoose={(choice) =>
                context.decide(
                  approval.approval_id,
                  "approved",
                  choice,
                  approval.subject_activity_id,
                )
              }
              onDecline={() =>
                context.decide(approval.approval_id, "rejected", "", approval.subject_activity_id)
              }
            />
          )
        ) : (
          <ApprovalActionsBar
            approval={{
              approval_id: approval.approval_id,
              payload: { tool_name: item.subject_label, note: item.risk_summary },
            }}
            disabled={!enabled}
            onSend={(message, feedback) => {
              const reject = message.startsWith("reject");
              return context.decide(
                approval.approval_id,
                reject ? "rejected" : "approved",
                feedback ?? (reject ? message.slice(message.indexOf(":") + 1).trim() : ""),
                approval.subject_activity_id,
              );
            }}
          />
        ))}
    </section>
  );
}

export function DecisionNotice({ result }: { result: DecisionResult | { state: "checking" } }) {
  const t = useTranslations("executionDetail");
  const labels = {
    checking: t("decision.checking"),
    unknown: t("decision.unknown"),
    unavailable: t("decision.unavailable"),
    stale: t("decision.stale"),
  };
  return (
    <p role="status" className="text-muted-foreground text-xs">
      {result.state === "settled"
        ? t("recordedDecision", { status: result.item.decision ?? result.item.status })
        : labels[result.state]}
    </p>
  );
}
