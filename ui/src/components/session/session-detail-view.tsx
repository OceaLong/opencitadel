"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Layers, Loader2, Settings2 } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ApprovalActionsBar } from "@/components/session/approval-actions-bar";
import { ChatInput } from "@/components/session/chat-input";
import { ClarificationCard } from "@/components/session/clarification-card";
import { FilePreviewPanel } from "@/components/session/file-preview-panel";
import { OperatorScopeDialog } from "@/components/session/operator-scope-dialog";
import { SessionHeader } from "@/components/session/session-header";
import { ThinkingToggle } from "@/components/session/thinking-toggle";
import { ToolPreviewPanel } from "@/components/session/tool-preview-panel";
import { VirtualizedTimeline } from "@/components/session/virtualized-timeline";
import { VNCOverlay } from "@/components/session/vnc-overlay";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { getToolKind } from "@/components/tool-use/utils";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import {
  SessionContextPanel,
  useSessionContextRefs,
} from "@/components/workspace/session-context-panel";

import { useIsMobile } from "@/hooks/use-mobile";
import { useSessionDetailView } from "@/hooks/use-session-detail-view";
import { sessionApi } from "@/lib/api/session";
import type { SessionMode } from "@/lib/api/types";
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
  const { isMobile, isReady } = useIsMobile();
  const [mode, setMode] = useState<SessionMode>("ask");
  const [operatorScopeOpen, setOperatorScopeOpen] = useState(false);
  const [contextSheetOpen, setContextSheetOpen] = useState(false);
  const [savingOperatorScope, setSavingOperatorScope] = useState(false);
  const { kbSourceRef, handleTimelineSourceClick } = useSessionContextRefs();
  const {
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
    streaming,
    activeSkill,
    setActiveSkill,
    configEditable,
    timeline,
    sessionArtifacts,
    latestApproval,
    latestAsk,
    observationSummary,
    fileListOpen,
    setFileListOpen,
    previewFile,
    resolvedPreviewTool,
    vncOpen,
    hasPreview,
    chatInputRef,
    scrollContainerRef,
    handleSend,
    handleApprovalSend,
    handleAskSend,
    handleThinkingChange,
    handleModelChange,
    handleSkillChange,
    handleViewAllFiles,
    handleFileClick,
    handleToolClick,
    handleClosePreview,
    handleJumpToLatest,
    handleOpenVNC,
    handleCloseVNC,
    handleStop,
  } = useSessionDetailView({
    sessionId,
    initialMessage,
    initialAttachments,
    hasInitialMessage,
    mode,
  });

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

  const previewPanel = (
    <>
      {previewFile && <FilePreviewPanel file={previewFile} onClose={handleClosePreview} />}
      {resolvedPreviewTool || sessionArtifacts.length > 0 ? (
        <ToolPreviewPanel
          sessionId={sessionId}
          tool={resolvedPreviewTool}
          artifacts={sessionArtifacts}
          onClose={handleClosePreview}
          onJumpToLatest={handleJumpToLatest}
          onOpenVNC={
            resolvedPreviewTool && getToolKind(resolvedPreviewTool) === "browser"
              ? handleOpenVNC
              : undefined
          }
        />
      ) : null}
    </>
  );

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

  const showMobilePanels = !isReady || isMobile;

  return (
    <>
      <div className="flex h-full w-full flex-row overflow-hidden">
        <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
          <div
            className={`mx-auto flex h-full w-full min-w-0 flex-col px-4 ${hasPreview && !showMobilePanels ? "" : hasContext ? "" : "max-w-content"}`}
          >
            <div className="flex-shrink-0">
              <SessionHeader
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
                  hasContext && showMobilePanels ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-8 gap-1.5 rounded-full px-2.5 md:hidden"
                      onClick={() => setContextSheetOpen(true)}
                    >
                      <Layers className="size-3.5" />
                      {t("contextPanel")}
                    </Button>
                  ) : undefined
                }
              />
            </div>

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
                          disabled={savingOperatorScope}
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
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => refresh()}
                          >
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
                  onToolClick={handleToolClick}
                  streaming={streaming}
                  onSourceClick={hasContext ? handleTimelineSourceClick : undefined}
                />

                {(session?.status === "running" ||
                  (hasInitialMessage && timeline.length === 0)) && (
                  <div className="text-muted-foreground flex items-center gap-2 py-3 text-sm">
                    <Loader2 className="size-4 animate-spin" />
                    <span>{t("thinking")}</span>
                  </div>
                )}

                <div className="pb-mobile-nav min-h-[140px] md:min-h-[140px] md:pb-0" />
              </div>
            </div>

            <div className="bg-background/95 flex-shrink-0 py-4">
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
              {latestAsk ? (
                <ClarificationCard
                  key={latestAsk.ask_id}
                  className="mb-2"
                  question={latestAsk.question}
                  choices={latestAsk.choices}
                  onChoose={(choice) => handleAskSend(choice)}
                  onDecline={() => handleAskSend(null)}
                  disabled={streaming}
                />
              ) : latestApproval ? (
                <ApprovalActionsBar
                  key={latestApproval.approval_id}
                  className="mb-2"
                  approval={latestApproval}
                  onSend={handleApprovalSend}
                  disabled={streaming}
                />
              ) : null}
              <ChatInput
                ref={chatInputRef}
                onSend={handleSend}
                sessionId={sessionId}
                isRunning={session?.status === "running"}
                onStop={handleStop}
                toolbarRight={
                  <>
                    <ThinkingToggle
                      enabled={session?.thinking_enabled ?? false}
                      onChange={handleThinkingChange}
                      disabled={!configEditable && session.status === "running"}
                    />
                    <SessionModelPicker
                      value={session.model_id}
                      onChange={handleModelChange}
                      disabled={!configEditable && session.status === "running"}
                    />
                    <SessionSkillPicker
                      value={session.skill_id}
                      onChange={handleSkillChange}
                      onSkillLoaded={setActiveSkill}
                      disabled={!configEditable && session.status === "running"}
                    />
                  </>
                }
              />
            </div>
          </div>
        </div>

        {hasPreview && !showMobilePanels && (
          <div className="animate-in slide-in-from-right h-full min-h-0 w-full max-w-[600px] flex-shrink-0 overflow-hidden duration-300">
            {previewPanel}
          </div>
        )}

        {hasContext && !showMobilePanels && (
          <div className={hasPreview ? "hidden" : undefined}>
            <SessionContextPanel
              knowledgeBaseId={knowledgeBaseId}
              sessionId={session.session_id}
              resourceBindings={session.resource_bindings}
              kbSourceRef={kbSourceRef}
            />
          </div>
        )}
      </div>

      {showMobilePanels && (
        <Sheet open={hasPreview} onOpenChange={(open) => !open && handleClosePreview()}>
          <SheetContent
            side="right"
            className="w-full max-w-full overflow-hidden p-2 sm:max-w-[600px]"
          >
            {previewPanel}
          </SheetContent>
        </Sheet>
      )}

      {showMobilePanels && hasContext && (
        <Sheet open={contextSheetOpen} onOpenChange={setContextSheetOpen}>
          <SheetContent side="right" className="w-full max-w-full overflow-hidden p-0 sm:max-w-md">
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

      {vncOpen && <VNCOverlay sessionId={sessionId} onClose={handleCloseVNC} />}

      <OperatorScopeDialog
        open={operatorScopeOpen}
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
