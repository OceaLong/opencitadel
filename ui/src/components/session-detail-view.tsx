"use client";

import { Loader2 } from "lucide-react";

import { ChatInput } from "@/components/chat-input";
import { ChatMessage } from "@/components/chat-message";
import { FilePreviewPanel } from "@/components/file-preview-panel";
import { PlanPanel } from "@/components/plan-panel";
import { SessionHeader } from "@/components/session-header";
import { SessionModelPicker } from "@/components/session-model-picker";
import { SessionSkillPicker } from "@/components/session-skill-picker";
import { ThinkingToggle } from "@/components/thinking-toggle";
import { ToolPreviewPanel } from "@/components/tool-preview-panel";
import { getToolKind } from "@/components/tool-use/utils";
import { VNCOverlay } from "@/components/vnc-overlay";

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
  const {
    session,
    files,
    events,
    loading,
    error,
    refresh,
    refreshFiles,
    streaming,
    activeSkill,
    setActiveSkill,
    configEditable,
    timeline,
    planSteps,
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
  });

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
        {/* 主内容区 */}
        <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
          <div
            className={`mx-auto flex h-full w-full min-w-0 flex-col px-4 ${hasPreview ? "" : "max-w-[768px]"}`}
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
              />
            </div>

            <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
              <div className="flex w-full flex-col gap-3 pt-3">
                {timeline.length === 0 && !streaming && !hasInitialMessage && (
                  <div className="text-muted-foreground flex items-center justify-center py-8 text-sm">
                    暂无对话记录，在下方输入任务或提问
                  </div>
                )}
                {timeline.map((item) => (
                  <ChatMessage
                    key={item.id}
                    item={item}
                    onViewAllFiles={handleViewAllFiles}
                    onFileClick={handleFileClick}
                    onToolClick={handleToolClick}
                  />
                ))}

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

        {/* 文件预览面板 */}
        {previewFile && (
          <div className="animate-in slide-in-from-right h-full w-[600px] flex-shrink-0 duration-300">
            <FilePreviewPanel file={previewFile} onClose={handleClosePreview} />
          </div>
        )}

        {/* 工具预览面板 */}
        {resolvedPreviewTool && (
          <div className="animate-in slide-in-from-right h-full w-[600px] flex-shrink-0 py-2 pr-2 duration-300">
            <ToolPreviewPanel
              tool={resolvedPreviewTool}
              onClose={handleClosePreview}
              onJumpToLatest={handleJumpToLatest}
              onOpenVNC={getToolKind(resolvedPreviewTool) === "browser" ? handleOpenVNC : undefined}
            />
          </div>
        )}
      </div>

      {/* noVNC 全屏远程桌面覆盖层 */}
      {vncOpen && <VNCOverlay sessionId={sessionId} onClose={handleCloseVNC} />}
    </>
  );
}
