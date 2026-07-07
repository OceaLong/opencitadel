"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import type { ChatInputRef } from "@/components/chat-input";
import { getToolKind } from "@/components/tool-use/utils";

import { useIncrementalTimeline } from "@/hooks/use-incremental-timeline";
import { useSessionDetail } from "@/hooks/use-session-detail";
import { useRequireAuth } from "@/hooks/use-require-auth";
import type { Locale } from "@/i18n/routing";
import { sessionApi } from "@/lib/api/session";
import type {
  ApprovalEventData,
  ArtifactEventSummary,
  ClarifyAnswer,
  FileInfo,
  SessionCheckpoint,
  SessionMode,
  Skill,
  ToolEvent,
  SSEEventData,
} from "@/lib/api/types";
import type { AttachmentFile, TimelineItem } from "@/lib/session-events";
import { getLatestPlanFromEvents, getTaskObservationSummary } from "@/lib/session-events";

export type UseSessionDetailViewOptions = {
  sessionId: string;
  initialMessage?: string;
  initialAttachments?: string[];
  hasInitialMessage?: boolean;
  mode?: SessionMode;
};

function findLatestTool(timeline: TimelineItem[]): ToolEvent | null {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const item = timeline[i];
    if (item.kind === "tool" && getToolKind(item.data) !== "message") {
      return item.data;
    }
    if (item.kind === "step" && item.tools.length > 0) {
      for (let j = item.tools.length - 1; j >= 0; j--) {
        if (getToolKind(item.tools[j]) !== "message") {
          return item.tools[j];
        }
      }
    }
  }
  return null;
}

function getSessionArtifactsFromEvents(events: SSEEventData[]): ArtifactEventSummary[] {
  const map = new Map<string, ArtifactEventSummary>();
  for (const ev of events) {
    if (ev.type !== "artifact") continue;
    const data = ev.data;
    const existing = map.get(data.artifact_id);
    if (!existing || data.version >= existing.version) {
      map.set(data.artifact_id, {
        artifact_id: data.artifact_id,
        kind: data.kind,
        title: data.title,
        status: data.status,
        storage_ref: data.storage_ref,
        version: data.version,
      });
    }
  }
  return Array.from(map.values());
}

function getLatestApprovalFromEvents(
  events: SSEEventData[],
  waiting: boolean,
): ApprovalEventData | null {
  if (!waiting) return null;
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (ev.type === "approval") return ev.data;
  }
  return null;
}

export function useSessionDetailView({
  sessionId,
  initialMessage,
  initialAttachments,
  hasInitialMessage,
  mode,
}: UseSessionDetailViewOptions) {
  const router = useRouter();
  const locale = useLocale() as Locale;
  const t = useTranslations("sessionDetail");
  const tAuth = useTranslations("auth");
  const { requireAuth } = useRequireAuth();
  const [includeDebug, setIncludeDebug] = useState(false);
  const detail = useSessionDetail(sessionId, hasInitialMessage);
  const {
    session,
    files,
    events,
    checkpoints,
    loading,
    loadingEarlier,
    hasEarlierHistory,
    error,
    refresh,
    loadEarlierEvents,
    refreshFiles,
    sendMessage,
    updateSessionConfig,
    streaming,
    streamStatus,
    streamError,
    enableDebugStream,
    refetchEventsWithDebug,
  } = detail;

  const [activeSkill, setActiveSkill] = useState<Skill | null>(null);
  const [fileListOpen, setFileListOpen] = useState(false);
  const [previewFile, setPreviewFile] = useState<AttachmentFile | null>(null);
  const [previewTool, setPreviewTool] = useState<ToolEvent | null>(null);
  const [artifactsPreviewDismissed, setArtifactsPreviewDismissed] = useState(false);
  const [vncOpen, setVncOpen] = useState(false);
  const [restoringCheckpoint, setRestoringCheckpoint] = useState(false);
  const [checkpointDialogOpen, setCheckpointDialogOpen] = useState(false);
  const [pendingCheckpoint, setPendingCheckpoint] = useState<SessionCheckpoint | null>(null);
  const initialMessageSentRef = useRef(false);
  const chatInputRef = useRef<ChatInputRef>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevToolCountRef = useRef(0);

  const configEditable = session?.status === "pending" || session?.status === "completed" || session?.status === "failed";
  const timeline = useIncrementalTimeline(events, locale);
  const checkpointByAnchor = useMemo(() => {
    const map = new Map<string, SessionCheckpoint>();
    for (const checkpoint of checkpoints) {
      map.set(checkpoint.anchor_event_id, checkpoint);
    }
    return map;
  }, [checkpoints]);
  const planSteps = useMemo(() => getLatestPlanFromEvents(events), [events]);
  const sessionArtifacts = useMemo(() => getSessionArtifactsFromEvents(events), [events]);
  const sessionArtifactsKey = useMemo(
    () => sessionArtifacts.map((item) => `${item.artifact_id}:${item.version}`).join("|"),
    [sessionArtifacts],
  );
  const latestApproval = useMemo(
    () => getLatestApprovalFromEvents(events, session?.status === "waiting"),
    [events, session?.status],
  );
  const observationSummary = useMemo(
    () => getTaskObservationSummary(events, session?.status),
    [events, session?.status],
  );
  const hasPreview =
    previewFile !== null ||
    previewTool !== null ||
    (sessionArtifacts.length > 0 && !artifactsPreviewDismissed);

  useEffect(() => {
    setArtifactsPreviewDismissed(false);
  }, [sessionArtifactsKey]);

  const resolvedPreviewTool = useMemo(() => {
    if (!previewTool) return null;
    const id = (previewTool as { tool_call_id?: string }).tool_call_id;
    if (!id) return previewTool;

    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i];
      if (item.kind === "tool" && (item.data as { tool_call_id?: string }).tool_call_id === id) {
        return item.data;
      }
      if (item.kind === "step") {
        for (const tool of item.tools) {
          if ((tool as { tool_call_id?: string }).tool_call_id === id) return tool;
        }
      }
    }
    return previewTool;
  }, [previewTool, timeline]);

  useEffect(() => {
    if (session?.status !== "running" || vncOpen) return;

    const latestTool = findLatestTool(timeline);
    const toolCount = timeline.reduce((count, item) => {
      if (item.kind === "tool") return count + 1;
      if (item.kind === "step") return count + item.tools.length;
      return count;
    }, 0);

    if (toolCount > prevToolCountRef.current && latestTool) {
      queueMicrotask(() => {
        setPreviewTool(latestTool);
        setPreviewFile(null);
        scrollContainerRef.current?.scrollTo({
          top: scrollContainerRef.current.scrollHeight,
          behavior: "smooth",
        });
      });
    }
    prevToolCountRef.current = toolCount;
  }, [timeline, session?.status, vncOpen]);

  useEffect(() => {
    if (initialMessage && !initialMessageSentRef.current && session && !loading && !streaming) {
      initialMessageSentRef.current = true;
      sendMessage(initialMessage, initialAttachments || [])
        .then(() => {
          setTimeout(() => {
            router.replace(`/sessions/${sessionId}`);
          }, 100);
        })
        .catch((e) => {
          toast.error(e instanceof Error ? e.message : t("sendMessageFailed"));
        });
    }
  }, [
    initialMessage,
    initialAttachments,
    session,
    loading,
    streaming,
    sendMessage,
    sessionId,
    router,
  ]);

  const sessionModelId = session?.model_id || undefined;
  const sessionSkillId = session?.skill_id || undefined;
  const sessionThinkingEnabled = session?.thinking_enabled ?? false;

  const handleSend = useCallback(
    async (message: string, uploadedFiles: FileInfo[]) => {
      if (!requireAuth(tAuth("loginToSendMessage"))) return;
      try {
        const attachmentIds = uploadedFiles.map((file) => file.id);
        await sendMessage(message, attachmentIds, {
          model_id: sessionModelId,
          skill_id: sessionSkillId,
          thinking_enabled: sessionThinkingEnabled,
          mode,
        });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t("sendFailedRetry"));
        throw e;
      }
    },
    [sendMessage, sessionModelId, sessionSkillId, sessionThinkingEnabled, mode, requireAuth, t, tAuth],
  );

  const handleClarifyAnswer = useCallback(
    async (answer: string, clarifyAnswers?: ClarifyAnswer[]) => {
      if (!requireAuth(tAuth("loginToSendMessage"))) return;
      await sendMessage(answer, [], {
        model_id: sessionModelId,
        skill_id: sessionSkillId,
        thinking_enabled: sessionThinkingEnabled,
        clarify_answers: clarifyAnswers,
        mode,
      });
    },
    [sendMessage, sessionModelId, sessionSkillId, sessionThinkingEnabled, mode, requireAuth, t, tAuth],
  );

  const handleGateSend = useCallback(
    async (message: string) => {
      if (!requireAuth(tAuth("loginToSendMessage"))) return;
      await sendMessage(message, [], {
        model_id: sessionModelId,
        skill_id: sessionSkillId,
        thinking_enabled: sessionThinkingEnabled,
        mode,
      });
    },
    [sendMessage, sessionModelId, sessionSkillId, sessionThinkingEnabled, mode, requireAuth, t, tAuth],
  );

  const handleThinkingChange = useCallback(
    async (enabled: boolean) => {
      await updateSessionConfig({ thinking_enabled: enabled });
    },
    [updateSessionConfig],
  );

  const handleModelChange = useCallback(
    async (modelId: string | undefined) => {
      if (!modelId) return;
      await updateSessionConfig({ model_id: modelId });
    },
    [updateSessionConfig],
  );

  const handleSkillChange = useCallback(
    async (skillId: string | undefined) => {
      await updateSessionConfig({ skill_id: skillId ?? "" });
    },
    [updateSessionConfig],
  );

  const handleViewAllFiles = useCallback(() => {
    refreshFiles();
    setFileListOpen(true);
  }, [refreshFiles]);

  const handleFileClick = useCallback((file: AttachmentFile) => {
    setPreviewFile(file);
    setPreviewTool(null);
  }, []);

  const handleToolClick = useCallback((tool: ToolEvent) => {
    const kind = getToolKind(tool);
    if (kind === "message") return;
    setPreviewTool(tool);
    setPreviewFile(null);
  }, []);

  const handleClosePreview = useCallback(() => {
    setPreviewFile(null);
    setPreviewTool(null);
    setArtifactsPreviewDismissed(true);
  }, []);

  const handleJumpToLatest = useCallback(() => {
    const latest = findLatestTool(timeline);
    if (latest) {
      setPreviewTool(latest);
      setPreviewFile(null);
    }
    scrollContainerRef.current?.scrollTo({
      top: scrollContainerRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [timeline]);

  const handleOpenVNC = useCallback(() => {
    setVncOpen(true);
  }, []);

  const handleCloseVNC = useCallback(() => {
    setVncOpen(false);
    const latest = findLatestTool(timeline);
    if (latest && session?.status === "running") {
      setPreviewTool(latest);
      setPreviewFile(null);
      setTimeout(() => {
        scrollContainerRef.current?.scrollTo({
          top: scrollContainerRef.current.scrollHeight,
          behavior: "smooth",
        });
      }, 100);
    }
  }, [timeline, session?.status]);

  const handleStop = useCallback(async () => {
    if (!session) return;
    try {
      await sessionApi.stopSession(sessionId);
      toast.success(t("taskStopped"));
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("stopTaskFailed"));
    }
  }, [session, sessionId, refresh, t]);

  const handleDebugOpen = useCallback(() => {
    setIncludeDebug(true);
    enableDebugStream();
    void refetchEventsWithDebug();
  }, [enableDebugStream, refetchEventsWithDebug]);

  const handleRestoreCheckpoint = useCallback((checkpoint: SessionCheckpoint) => {
    if (!session) return;
    setPendingCheckpoint(checkpoint);
    setCheckpointDialogOpen(true);
  }, [session]);

  const confirmRestoreCheckpoint = useCallback(async () => {
    if (!session || !pendingCheckpoint) return;
    setRestoringCheckpoint(true);
    try {
      if (session.status === "running") {
        await sessionApi.stopSession(sessionId);
      }
      await sessionApi.restoreCheckpoint(sessionId, pendingCheckpoint.id);
      toast.success(t("checkpointRestored"));
      setCheckpointDialogOpen(false);
      setPendingCheckpoint(null);
      await refresh();
      await refreshFiles();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("restoreFailed"));
    } finally {
      setRestoringCheckpoint(false);
    }
  }, [session, pendingCheckpoint, sessionId, refresh, refreshFiles, t]);

  const resolveCheckpoint = useCallback(
    (anchorEventId?: string) => {
      if (!anchorEventId) return undefined;
      return checkpointByAnchor.get(anchorEventId);
    },
    [checkpointByAnchor],
  );

  return {
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
    loadEarlierEvents,
    refreshFiles,
    streaming,
    activeSkill,
    setActiveSkill,
    configEditable,
    timeline,
    planSteps,
    sessionArtifacts,
    latestApproval,
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
    handleGateSend,
    handleClarifyAnswer,
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
    includeDebug,
    handleDebugOpen,
    resolveCheckpoint,
    handleRestoreCheckpoint,
    restoringCheckpoint,
    checkpointDialogOpen,
    setCheckpointDialogOpen,
    pendingCheckpoint,
    confirmRestoreCheckpoint,
  };
}
