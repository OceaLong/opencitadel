"use client";

import { Loader2 } from "lucide-react";

import { ChatInput } from "@/components/chat-input";
import { CheckpointRestoreDialog } from "@/components/checkpoint-restore-dialog";
import { FilePreviewPanel } from "@/components/file-preview-panel";
import { PlanPanel } from "@/components/plan-panel";
import { SessionHeader } from "@/components/session-header";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { ThinkingToggle } from "@/components/thinking-toggle";
import { ToolPreviewPanel } from "@/components/tool-preview-panel";
import { getToolKind } from "@/components/tool-use/utils";
import { VirtualizedTimeline } from "@/components/virtualized-timeline";
import { VNCOverlay } from "@/components/vnc-overlay";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";

import { useIsMobile } from "@/hooks/use-mobile";
import { useSessionDetailView } from "@/hooks/use-session-detail-view";

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
  const isMobile = useIsMobile();
  const {
    session,
    files,
    events,
    loading,
    loadingEarlier,
    hasEarlierHistory,
    error,
    refresh,
    refreshFiles,
    loadEarlierEvents,
    streaming,
    activeSkill,
    setActiveSkill,
    configEditable,
    timeline,
    planSteps,
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
    handleThinkingChange,
    handleModelChange,
    handleSkillChange,
    handleViewAllFiles,
    handleFileClick,
    handleToolClick,
    handleClarifyAnswer,
    handleClosePreview,
    handleJumpToLatest,
    handleOpenVNC,
    handleCloseVNC,
    handleStop,
    handleDebugOpen,
    resolveCheckpoint,
    handleRestoreCheckpoint,
    restoringCheckpoint,
    checkpointDialogOpen,
    setCheckpointDialogOpen,
    pendingCheckpoint,
    confirmRestoreCheckpoint,
  } = useSessionDetailView({
    sessionId,
    initialMessage,
    initialAttachments,
    hasInitialMessage,
  });

  const previewPanel = (
    <>
      {previewFile && (
        <FilePreviewPanel file={previewFile} onClose={handleClosePreview} />
      )}
      {resolvedPreviewTool && (
        <ToolPreviewPanel
          tool={resolvedPreviewTool}
          onClose={handleClosePreview}
          onJumpToLatest={handleJumpToLatest}
          onOpenVNC={getToolKind(resolvedPreviewTool) === "browser" ? handleOpenVNC : undefined}
        />
      )}
    </>
  );

  if (loading && !session) {
    return (
      <div className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-center px-4">
        {hasInitialMessage ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            <span>正在思考中...</span>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">加载中...</p>
        )}
      </div>
    );
  }

  if (error && !session) {
    return (
      <div className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-center gap-2 px-4">
        <p className="text-sm text-red-600">{error.message}</p>
        <button type="button" onClick={() => refresh()} className="text-primary text-sm underline">
          重试
        </button>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-center px-4">
        <p className="text-muted-foreground text-sm">未找到该任务</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex h-screen w-full flex-row overflow-hidden">
        <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
          <div
            className={`mx-auto flex h-full w-full min-w-0 flex-col px-4 ${hasPreview && !isMobile ? "" : "max-w-[768px]"}`}
          >
            <div className="flex-shrink-0">
              <SessionHeader
                title={session.title}
                files={files}
                fileListOpen={fileListOpen}
                onFileListOpenChange={setFileListOpen}
                onFetchFiles={refreshFiles}
                onFileClick={handleFileClick}
                sessionId={sessionId}
                memoryEditable={configEditable}
                tokenUsage={session.token_usage}
                events={events}
                observationSummary={observationSummary}
                onDebugOpen={handleDebugOpen}
              />
            </div>

            <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
              <div className="flex w-full flex-col gap-3 pt-3">
                {session.status === "failed" && (
                  <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-lg border px-3 py-2 text-sm">
                    任务执行失败，可修改配置后重新发送消息继续。
                  </div>
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
                      {loadingEarlier ? "加载中..." : "加载更早对话"}
                    </Button>
                  </div>
                )}

                {timeline.length === 0 && !streaming && !hasInitialMessage && (
                  <div className="text-muted-foreground flex items-center justify-center py-8 text-sm">
                    暂无对话记录，在下方输入任务或提问
                  </div>
                )}

                <VirtualizedTimeline
                  timeline={timeline}
                  scrollContainerRef={scrollContainerRef}
                  sessionStatus={session.status}
                  onViewAllFiles={handleViewAllFiles}
                  onFileClick={handleFileClick}
                  onToolClick={handleToolClick}
                  onClarifyAnswer={handleClarifyAnswer}
                  resolveCheckpoint={resolveCheckpoint}
                  onRestoreCheckpoint={handleRestoreCheckpoint}
                  restoringCheckpoint={restoringCheckpoint}
                  streaming={streaming}
                />

                {(session?.status === "running" ||
                  (hasInitialMessage && timeline.length === 0)) && (
                  <div className="text-muted-foreground flex items-center gap-2 py-3 text-sm">
                    <Loader2 className="size-4 animate-spin" />
                    <span>正在思考中...</span>
                  </div>
                )}

                <div className="h-[140px]" />
              </div>
            </div>

            <div className="bg-background/95 flex-shrink-0 py-4">
              {activeSkill && activeSkill.examples.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2 px-1">
                  {activeSkill.examples.map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      className="border-border/60 bg-card text-muted-foreground hover:bg-muted/70 hover:text-foreground rounded-full border px-2.5 py-1 text-xs shadow-[var(--shadow-card)] transition-colors"
                      onClick={() => chatInputRef.current?.setInputText(ex)}
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              )}
              <PlanPanel className="mb-2" steps={planSteps} />
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

        {hasPreview && !isMobile && (
          <div className="animate-in slide-in-from-right h-full w-full max-w-[600px] flex-shrink-0 duration-300">
            {previewPanel}
          </div>
        )}
      </div>

      {isMobile && (
        <Sheet open={hasPreview} onOpenChange={(open) => !open && handleClosePreview()}>
          <SheetContent side="right" className="w-full max-w-full p-2 sm:max-w-[600px]">
            {previewPanel}
          </SheetContent>
        </Sheet>
      )}

      {vncOpen && <VNCOverlay sessionId={sessionId} onClose={handleCloseVNC} />}

      <CheckpointRestoreDialog
        checkpoint={pendingCheckpoint}
        open={checkpointDialogOpen}
        restoring={restoringCheckpoint}
        onOpenChange={setCheckpointDialogOpen}
        onConfirm={confirmRestoreCheckpoint}
      />
    </>
  );
}
