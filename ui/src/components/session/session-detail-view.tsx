"use client";
import { useEffect, useLayoutEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Layers, Loader2, Settings2 } from "lucide-react";
import { toast } from "sonner";

import { AnalysisReturnLink } from "@/components/analysis/return-link";
import { EmptyState } from "@/components/empty-state";
import { CaptureCaseAction } from "@/components/evaluation/capture-case-dialog";
import { EvaluationResultEntry } from "@/components/evaluation/result-entry";
import { DecisionNotice } from "@/components/execution/approval-detail";
import { ArtifactPanel } from "@/components/execution/artifact-panel";
import { DetailPanel } from "@/components/execution/detail-panel";
import { PlaybackControls } from "@/components/execution/playback-controls";
import { TaskView } from "@/components/execution/task-view";
import { TraceView } from "@/components/execution/trace-view";
import { WorkbenchShell } from "@/components/execution/workbench-shell";
import { MarkdownContent } from "@/components/markdown-content";
import { ApprovalActionsBar } from "@/components/session/approval-actions-bar";
import { ChatInput } from "@/components/session/chat-input";
import { ClarificationCard } from "@/components/session/clarification-card";
import { FilePreviewPanel } from "@/components/session/file-preview-panel";
import { OperatorScopeDialog } from "@/components/session/operator-scope-dialog";
import { SessionHeader } from "@/components/session/session-header";
import { ThinkingToggle } from "@/components/session/thinking-toggle";
import { VirtualizedTimeline } from "@/components/session/virtualized-timeline";
import { VNCOverlay } from "@/components/session/vnc-overlay";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import {
  SessionContextPanel,
  useSessionContextRefs,
} from "@/components/workspace/session-context-panel";

import { useExecutionApprovalDecision } from "@/hooks/use-execution-approval-decision";
import { useSessionDetailView } from "@/hooks/use-session-detail-view";
import { useSessionRuns } from "@/hooks/use-session-runs";
import { sessionApi } from "@/lib/api/session";
import type { SessionMode } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";
import { useReportPageTitle } from "@/providers/page-title-provider";

export type SessionDetailViewProps = {
  sessionId: string;
  initialMessage?: string;
  initialAttachments?: string[];
  hasInitialMessage?: boolean;
};

export function SessionDetailView({
  sessionId,
  initialMessage,
  initialAttachments,
  hasInitialMessage,
}: SessionDetailViewProps) {
  const t = useTranslations("sessionDetail");
  const tCommon = useTranslations("common");
  const tw = useTranslations("executionWorkbench");
  const { user } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const td = useTranslations("executionDetail");
  const [vncAuthority, setVncAuthority] = useState<string | null>(null);
  const [mode, setMode] = useState<SessionMode>("ask");
  const [operatorScopeOpen, setOperatorScopeOpen] = useState(false);
  const [contextSheetOpen, setContextSheetOpen] = useState(false);
  const [savingOperatorScope, setSavingOperatorScope] = useState(false);
  const { kbSourceRef, handleTimelineSourceClick } = useSessionContextRefs();
  const {
    workbench,
    session,
    files,
    events,
    loading,
    loadingEarlier,
    hasEarlierHistory,
    error,
    streamStatus,
    streamError,
    refresh,
    refreshFiles,
    loadEarlierEvents,
    admissionPending,
    admissionRevision,
    streaming,
    activeSkill,
    setActiveSkill,
    configEditable,
    timeline,
    latestApproval: observedApproval,
    latestAsk: observedAsk,
    observationSummary,
    fileListOpen,
    setFileListOpen,
    previewFile,
    hasPreview,
    chatInputRef,
    scrollContainerRef,
    handleSend,
    handleThinkingChange,
    handleModelChange,
    handleSkillChange,
    handleViewAllFiles,
    handleFileClick,
    handleToolClick,
    handleClosePreview,
    handleStop,
  } = useSessionDetailView({
    sessionId,
    initialMessage,
    initialAttachments,
    hasInitialMessage,
    mode,
  });

  const runs = useSessionRuns(sessionId, events, admissionPending, admissionRevision);

  useEffect(() => {
    if (session?.mode) {
      setMode(session.mode);
    }
  }, [session?.mode]);

  useReportPageTitle(session?.title ?? undefined);

  const knowledgeBaseId = session?.resource_bindings?.find(
    (binding) => binding.resource_kind === "knowledge_base",
  )?.resource_id;
  const hasContext = Boolean(knowledgeBaseId);

  const handleOperatorScopeSave = async (config: { operatorDomains: string[] }) => {
    setSavingOperatorScope(true);
    try {
      await sessionApi.updateSessionConfig(sessionId, {
        operator_domains: config.operatorDomains,
      });
      toast.success(t("operator.domainSettingsSaved"));
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tCommon("retry"));
    } finally {
      setSavingOperatorScope(false);
    }
  };

  const showOperatorPanel =
    Boolean(session?.operator_scope) || activeSkill?.slug === "web-operator";

  const selection = workbench.selection;
  const page = workbench.view;
  const confirmedEmpty =
    !selection.runId &&
    runs.state === "empty" &&
    session?.status === "pending" &&
    !admissionPending;
  const currentRun = Boolean(
    selection.runId &&
    runs.state === "ready" &&
    runs.items[0]?.run_id === selection.runId &&
    workbench.loadState === "ready",
  );
  const liveContext =
    !workbench.seeking &&
    workbench.loadState !== "forbidden" &&
    selection.at === null &&
    (currentRun || confirmedEmpty);
  const historical = !liveContext;
  const canAct = Boolean(user && user.global_role !== "auditor" && liveContext);
  const commandAuthority =
    canAct && currentRun && scope && scope.userId === user?.id
      ? JSON.stringify([sessionId, selection.runId, page?.at, scope.workspaceId, scopeRevision])
      : null;
  const decisions = useExecutionApprovalDecision({
    authority: commandAuthority,
    runId: selection.runId,
    sessionId,
    onChanged: () => {
      workbench.refresh();
      void refresh?.();
    },
  });
  const latestApproval = observedApproval?.run_id === selection.runId ? observedApproval : null;
  const latestAsk = observedAsk?.run_id === selection.runId ? observedAsk : null;
  const vncGrant =
    canAct && currentRun && session?.status === "running" && scope && scope.userId === user?.id
      ? JSON.stringify([sessionId, selection.runId, scope.workspaceId, scopeRevision])
      : null;
  useLayoutEffect(() => {
    setVncAuthority(null);
  }, [vncGrant]);
  const approvalResult = latestApproval ? decisions.results[latestApproval.approval_id] : undefined;
  const askResult = latestAsk ? decisions.results[latestAsk.ask_id] : undefined;
  const handleApprovalSend = async (message: string, feedback?: string) => {
    if (!latestApproval) return;
    const rejected = message.startsWith("reject") || message === "skip";
    const reason =
      rejected && message.includes(":") ? message.slice(message.indexOf(":") + 1).trim() : "";
    await decisions.decide(
      latestApproval.approval_id,
      rejected ? "rejected" : "approved",
      feedback ?? reason,
      typeof latestApproval.payload.subject_activity_id === "string"
        ? latestApproval.payload.subject_activity_id
        : page?.approvals?.find((item) => item.approval_id === latestApproval.approval_id)
            ?.subject_activity_id,
    );
  };
  const handleAskSend = async (choice: string | null) => {
    if (latestAsk)
      await decisions.decide(
        latestAsk.ask_id,
        choice === null ? "rejected" : "approved",
        choice ?? "",
        latestAsk.subject_activity_id ??
          page?.approvals?.find((item) => item.approval_id === latestAsk.ask_id)
            ?.subject_activity_id,
      );
  };
  const decisionNotice = (result: typeof approvalResult) =>
    result && <DecisionNotice result={result} />;
  const approvalControls = (
    <>
      {latestAsk && (
        <ClarificationCard
          key={`${commandAuthority}:${latestAsk.ask_id}`}
          className="mb-2"
          question={latestAsk.question}
          choices={latestAsk.choices}
          onChoose={handleAskSend}
          onDecline={() => handleAskSend(null)}
          disabled={
            streaming ||
            !commandAuthority ||
            Boolean(askResult && askResult.state !== "unavailable")
          }
        />
      )}
      {decisionNotice(askResult)}
      {latestApproval && (
        <ApprovalActionsBar
          key={`${commandAuthority}:${latestApproval.approval_id}`}
          className="mb-2"
          approval={latestApproval}
          onSend={handleApprovalSend}
          disabled={
            streaming ||
            !commandAuthority ||
            Boolean(approvalResult && approvalResult.state !== "unavailable")
          }
        />
      )}
      {decisionNotice(approvalResult)}
    </>
  );
  useEffect(() => {
    if (!liveContext) {
      setFileListOpen?.(false);
      setContextSheetOpen(false);
      setOperatorScopeOpen(false);
    }
  }, [liveContext, setFileListOpen]);

  if (loading && !session) {
    return (
      <div className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-center px-4">
        {hasInitialMessage ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            <span>{t("thinking")}</span>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">{tCommon("loading")}</p>
        )}
      </div>
    );
  }

  if (error && !session) {
    return (
      <div className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-center gap-2 px-4">
        <Alert variant="destructive" className="w-auto max-w-md">
          <AlertDescription className="flex flex-col items-center gap-2 text-center">
            <span>{error.message}</span>
            <Button type="button" variant="outline" size="sm" onClick={() => refresh()}>
              {tCommon("retry")}
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-center px-4">
        <p className="text-muted-foreground text-sm">{t("taskNotFound")}</p>
      </div>
    );
  }

  const selectStep = (stepId: string) =>
    workbench.setSelection({
      ...selection,
      stepId,
      panel: "overview",
      artifactId: null,
      version: null,
      citationId: null,
    });
  const returnToCurrentRun = () => {
    if (runs.state === "ready" && runs.items[0])
      workbench.setSelection({
        ...selection,
        runId: runs.items[0].run_id,
        at: null,
        stepId: null,
        panel: null,
        artifactId: null,
        version: null,
        citationId: null,
      });
    else {
      runs.refresh();
      workbench.returnToLive();
    }
  };
  const readonlyChat = (
    <div className="h-full overflow-y-auto p-3">
      <p className="text-muted-foreground mb-3 text-xs">{tw("historicalConversation")}</p>
      {page?.messages?.map((message) => (
        <MarkdownContent key={message.message_id} content={message.public_summary ?? ""} />
      ))}
      <Button variant="outline" onClick={returnToCurrentRun}>
        {tw("continueLive")}
      </Button>
    </div>
  );
  const chat = (
    <div className="flex h-full min-h-0 flex-col">
      {historical && readonlyChat}
      <div hidden={historical} className="flex h-full min-h-0 flex-col px-3">
        <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
          <div className="flex w-full flex-col gap-3 pt-3">
            {showOperatorPanel && (
              <Alert variant="info">
                <AlertDescription>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="space-y-1">
                      <p>
                        {t("operator.modeLabel")} ·{" "}
                        {session.operator_scope === "third_party_saas"
                          ? t("operator.thirdPartySaas")
                          : session.operator_scope === "owned"
                            ? t("operator.owned")
                            : t("operator.webOperator")}
                        {session.status === "waiting" && ` · ${t("operator.waitingApproval")}`}
                      </p>
                      {session.operator_domains && session.operator_domains.length > 0 && (
                        <p>
                          {t("operator.domainsLabel", {
                            domains: session.operator_domains.join(", "),
                          })}
                        </p>
                      )}
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 shrink-0 text-xs"
                      disabled={savingOperatorScope || !canAct}
                      onClick={() => setOperatorScopeOpen(true)}
                    >
                      <Settings2 className="size-3.5" />
                      {t("operator.editDomains")}
                    </Button>
                  </div>
                </AlertDescription>
              </Alert>
            )}
            {session.status === "failed" && (
              <Alert variant="destructive">
                <AlertDescription>{t("taskFailed")}</AlertDescription>
              </Alert>
            )}
            {session.status === "running" &&
              (streamStatus === "reconnecting" ||
                streamStatus === "stale" ||
                streamStatus === "error") && (
                <Alert variant="info">
                  <AlertDescription>
                    <div className="flex items-center justify-between gap-3">
                      <span>
                        {streamStatus === "stale"
                          ? t("streamStale")
                          : streamStatus === "error"
                            ? streamError?.message || t("streamError")
                            : t("streamReconnecting")}
                      </span>
                      <Button type="button" size="sm" variant="outline" onClick={() => refresh()}>
                        {t("resync")}
                      </Button>
                    </div>
                  </AlertDescription>
                </Alert>
              )}
            {hasEarlierHistory && (
              <div className="flex justify-center">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => loadEarlierEvents()}
                  disabled={loadingEarlier}
                >
                  {loadingEarlier ? tCommon("loading") : t("loadEarlier")}
                </Button>
              </div>
            )}

            {timeline.length === 0 && !streaming && !hasInitialMessage && (
              <EmptyState title={t("emptyTimeline")} className="h-full justify-center" />
            )}

            <VirtualizedTimeline
              timeline={timeline}
              scrollContainerRef={scrollContainerRef}
              onViewAllFiles={handleViewAllFiles}
              onFileClick={handleFileClick}
              onToolClick={(tool) => {
                handleToolClick(tool);
                const match = (workbench.trace?.steps ?? page?.steps ?? []).filter(
                  (step) => tool.tool_call_id && step.invocation_id === tool.tool_call_id,
                );
                if (match.length === 1) selectStep(match[0].step_id);
                else
                  workbench.setSelection({
                    ...selection,
                    stepId: null,
                    panel: "overview",
                    artifactId: null,
                    version: null,
                    citationId: null,
                  });
              }}
              streaming={streaming}
              onSourceClick={hasContext ? handleTimelineSourceClick : undefined}
            />

            {(session?.status === "running" || (hasInitialMessage && timeline.length === 0)) && (
              <div className="text-muted-foreground flex items-center gap-2 py-3 text-sm">
                <Loader2 className="size-4 animate-spin" />
                <span>{t("thinking")}</span>
              </div>
            )}

            <div className="pb-mobile-nav min-h-[140px] md:min-h-[140px] md:pb-0" />
          </div>
        </div>

        <div className="bg-background/95 max-h-[70%] shrink-0 overflow-y-auto py-4">
          {activeSkill && activeSkill.examples.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2 px-1">
              {activeSkill.examples.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  className="border-border/60 bg-card text-muted-foreground hover:bg-muted/70 hover:text-foreground shadow-card rounded-full border px-2.5 py-1 text-xs transition-colors"
                  onClick={() => chatInputRef.current?.setInputText(ex)}
                >
                  {ex}
                </button>
              ))}
            </div>
          )}
          {approvalControls}
          <ChatInput
            ref={chatInputRef}
            onSend={handleSend}
            disabled={!canAct}
            sessionId={sessionId}
            isRunning={session?.status === "running"}
            onStop={canAct ? handleStop : undefined}
            toolbarRight={
              <>
                <ThinkingToggle
                  enabled={session?.thinking_enabled ?? false}
                  onChange={handleThinkingChange}
                  disabled={!canAct || (!configEditable && session.status === "running")}
                />
                <SessionModelPicker
                  value={session.model_id}
                  onChange={handleModelChange}
                  disabled={!canAct || (!configEditable && session.status === "running")}
                />
                <SessionSkillPicker
                  value={session.skill_id}
                  onChange={handleSkillChange}
                  onSkillLoaded={setActiveSkill}
                  disabled={!canAct || (!configEditable && session.status === "running")}
                />
              </>
            }
          />
        </div>
      </div>
    </div>
  );
  const view = page ? (
    selection.view === "task" ? (
      <TaskView
        renderIdentity={{
          scopeId: scope?.workspaceId ?? "",
          at: page.at ?? "",
          ready: workbench.loadState === "ready" && !workbench.seeking && !workbench.error,
        }}
        run={page.run}
        steps={page.steps}
        approvals={page.approvals}
        artifacts={page.artifacts}
        artifactEntry={
          page.artifacts?.length && page.at && user && scope && user.id === scope.userId ? (
            <ArtifactPanel
              runId={selection.runId}
              at={page.at}
              owner={{
                key: JSON.stringify([
                  user.id,
                  scope.workspaceId,
                  scopeRevision,
                  selection.runId,
                  page.at,
                  page.revision,
                ]),
                workspaceId: scope.workspaceId,
                at: page.at,
              }}
              artifacts={page.artifacts}
              onRevoked={workbench.revokeContent}
              onSelectArtifact={(artifactId, version) =>
                workbench.setSelection({
                  ...selection,
                  artifactId,
                  version,
                  stepId: null,
                  citationId: null,
                  panel: "artifact",
                })
              }
              onSelectProducer={({ runId, stepId }) =>
                workbench.setSelection({
                  ...selection,
                  runId,
                  stepId,
                  artifactId: null,
                  version: null,
                  citationId: null,
                  panel: "overview",
                  at: runId === selection.runId ? selection.at : null,
                })
              }
              onSelectCitation={(citation) =>
                workbench.setSelection({
                  ...selection,
                  citationId: citation.citation_id,
                  artifactId: null,
                  version: null,
                  stepId: null,
                  panel: "source",
                })
              }
            />
          ) : undefined
        }
        selectedStepId={selection.stepId}
        onSelectStep={selectStep}
        onSelectCitation={(citationId, stepId) =>
          workbench.setSelection({
            ...selection,
            citationId,
            stepId,
            artifactId: null,
            version: null,
            panel: "source",
          })
        }
        onSelectArtifact={(artifactId, version) =>
          workbench.setSelection({
            ...selection,
            artifactId,
            version,
            panel: "artifact",
            stepId: null,
            citationId: null,
          })
        }
        onOpenApproval={() => workbench.setLayout({ ...workbench.layout, conversationOpen: true })}
        canApprove={canAct}
        approvalEntry={
          latestApproval && !historical ? (
            <Button
              variant="outline"
              onClick={() => workbench.setLayout({ ...workbench.layout, conversationOpen: true })}
            >
              {tw("openApproval")}
            </Button>
          ) : undefined
        }
        clarificationEntry={
          latestAsk && !historical ? (
            <Button
              variant="outline"
              onClick={() => workbench.setLayout({ ...workbench.layout, conversationOpen: true })}
            >
              {tw("openClarification")}
            </Button>
          ) : undefined
        }
      />
    ) : (
      <TraceView
        renderIdentity={{
          scopeId: scope?.workspaceId ?? "",
          runId: page.run.run_id,
          at: page.at ?? "",
          revision: page.revision,
          ready: workbench.loadState === "ready" && !workbench.seeking && !workbench.error,
        }}
        key={`${selection.runId}:${page.at}:${page.revision}`}
        steps={workbench.trace?.steps ?? page.steps}
        selection={selection.stepId}
        onSelectStep={selectStep}
        asOf={page.run.as_of}
        exhausted={workbench.trace?.exhausted ?? !page.next_cursor}
        complete={workbench.trace?.complete ?? page.run.completeness.state === "complete"}
        error={["error", "conflict", "rebuilding"].includes(workbench.loadState)}
        onRetry={workbench.refresh}
      />
    )
  ) : (
    <div
      data-testid={selection.view === "task" ? "task-view" : "trace-view"}
      className="space-y-3 p-4"
      role="status"
    >
      <p>{tw(`loadState.${workbench.loadState}`)}</p>
      {workbench.loadState === "loading" && (
        <>
          <div className="bg-muted h-24 rounded-md" />
          <div className="bg-muted h-24 rounded-md" />
        </>
      )}
      <Button
        variant="outline"
        onClick={() => {
          runs.refresh();
          workbench.refresh();
        }}
      >
        {tCommon("retry")}
      </Button>
    </div>
  );
  return (
    <>
      <WorkbenchShell
        onReturnLive={returnToCurrentRun}
        playback={
          <PlaybackControls
            at={selection.at}
            latestAvailable={workbench.latestAvailable}
            start={workbench.playbackRun?.admitted_at}
            currentTime={workbench.playbackRun?.as_of}
            coverage={workbench.playbackRun?.completeness}
            onSeekTime={workbench.seekTime}
            onSeekEvent={workbench.seekEvent}
            onReturnLive={returnToCurrentRun}
          />
        }
        selection={selection}
        onSelectionChange={workbench.setSelection}
        layout={workbench.layout}
        onLayoutChange={workbench.setLayout}
        runs={runs.items}
        runState={runs.state}
        onRefreshRuns={runs.refresh}
        onLoadMoreRuns={runs.nextCursor ? runs.loadMore : undefined}
        view={view}
        chat={chat}
        messageCount={timeline.length}
        clarificationCount={latestAsk ? 1 : 0}
        header={
          liveContext ? (
            <SessionHeader
              key={`${sessionId}:${selection.runId}`}
              files={files}
              fileListOpen={fileListOpen}
              onFileListOpenChange={setFileListOpen}
              onFetchFiles={refreshFiles}
              onFileClick={handleFileClick}
              sessionId={sessionId}
              tokenUsage={session.token_usage}
              events={events}
              observationSummary={observationSummary}
              leadingActions={
                <>
                  {selection.runId && page?.at && (
                    <>
                      <CaptureCaseAction runId={selection.runId} at={page.at} />
                      <EvaluationResultEntry runId={selection.runId} />
                      <AnalysisReturnLink />
                    </>
                  )}
                  {vncGrant && (
                    <Button size="sm" variant="outline" onClick={() => setVncAuthority(vncGrant)}>
                      {td("takeover")}
                    </Button>
                  )}
                  {hasContext ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-8 gap-1.5 rounded-full px-2.5"
                      onClick={() => setContextSheetOpen(true)}
                    >
                      <Layers className="size-3.5" />
                      {t("contextPanel")}
                    </Button>
                  ) : undefined}
                </>
              }
            />
          ) : selection.runId && page?.at ? (
            <div className="p-2">
              <CaptureCaseAction runId={selection.runId} at={page.at} />
              <EvaluationResultEntry runId={selection.runId} />
              <AnalysisReturnLink />
            </div>
          ) : undefined
        }
        notice={
          workbench.targetUnavailable ? (
            <Alert>
              <AlertDescription>
                {tw("targetUnavailable")}{" "}
                <Button variant="ghost" size="sm" onClick={workbench.dismissTargetNotice}>
                  {tw("dismiss")}
                </Button>
              </AlertDescription>
            </Alert>
          ) : workbench.issues?.length ? (
            <p role="status">{tw("invalidLocation")}</p>
          ) : undefined
        }
        detailOpen={Boolean(selection.panel || selection.stepId || (hasPreview && !historical))}
        onCloseDetail={() => {
          handleClosePreview();
          workbench.setSelection({
            ...selection,
            stepId: null,
            panel: null,
            artifactId: null,
            version: null,
            citationId: null,
          });
        }}
        detail={
          <>
            {previewFile && !historical && !selection.stepId && !selection.artifactId && (
              <FilePreviewPanel file={previewFile} onClose={handleClosePreview} />
            )}
            <DetailPanel
              renderReady={
                workbench.loadState === "ready" && !workbench.seeking && !workbench.error
              }
              selection={selection}
              run={page?.run ?? null}
              page={page}
              detail={workbench.detail}
              onReturnLive={returnToCurrentRun}
              onChanged={workbench.refresh}
              onRevoked={workbench.revokeContent}
              onSelectionChange={workbench.setSelection}
              historical={historical}
              approvalContext={{
                sessionId,
                allowed: Boolean(commandAuthority),
                decide: decisions.decide,
                results: decisions.results,
                ask: latestAsk,
              }}
            />
          </>
        }
      />
      {hasContext && liveContext && (
        <Sheet open={contextSheetOpen} onOpenChange={setContextSheetOpen}>
          <SheetContent side="right" className="w-full max-w-full overflow-hidden p-0 sm:max-w-md">
            <SheetTitle className="sr-only">{t("contextPanel")}</SheetTitle>
            <SheetDescription className="sr-only">{tw("contextDescription")}</SheetDescription>
            <SessionContextPanel
              knowledgeBaseId={knowledgeBaseId}
              sessionId={session.session_id}
              resourceBindings={session.resource_bindings}
              kbSourceRef={kbSourceRef}
              className="h-full w-full max-w-none border-0"
            />
          </SheetContent>
        </Sheet>
      )}

      {vncGrant && vncAuthority === vncGrant && (
        <VNCOverlay key={vncGrant} sessionId={sessionId} onClose={() => setVncAuthority(null)} />
      )}

      <OperatorScopeDialog
        open={operatorScopeOpen && canAct}
        onOpenChange={setOperatorScopeOpen}
        mode="edit"
        initialConfig={{
          scope: session?.operator_scope === "third_party_saas" ? "third_party_saas" : "owned",
          operatorDomains: session?.operator_domains ?? [],
        }}
        onConfirm={(config) => void handleOperatorScopeSave(config)}
      />
    </>
  );
}
