"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import type { ChatInputRef } from "@/components/chat-input";
import { getToolKind } from "@/components/tool-use/utils";

import { useSessionDetail } from "@/hooks/use-session-detail";
import { sessionApi } from "@/lib/api/session";
import type { FileInfo, SessionCheckpoint, Skill, ToolEvent } from "@/lib/api/types";
import type { AttachmentFile, TimelineItem } from "@/lib/session-events";
import {
  eventsToTimeline,
  getLatestPlanFromEvents,
  getTaskObservationSummary,
} from "@/lib/session-events";

export type UseSessionDetailViewOptions = {
  sessionId: string;
  initialMessage?: string;
  initialAttachments?: string[];
  hasInitialMessage?: boolean;
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

export function useSessionDetailView({
  sessionId,
  initialMessage,
  initialAttachments,
  hasInitialMessage,
}: UseSessionDetailViewOptions) {
  const router = useRouter();
  const [includeDebug, setIncludeDebug] = useState(false);
  const detail = useSessionDetail(sessionId, hasInitialMessage, includeDebug);
  const {
    session,
    files,
    events,
    checkpoints,
    loading,
    error,
    refresh,
    refreshFiles,
    sendMessage,
    updateSessionConfig,
    streaming,
  } = detail;

  const [activeSkill, setActiveSkill] = useState<Skill | null>(null);
  const [fileListOpen, setFileListOpen] = useState(false);
  const [previewFile, setPreviewFile] = useState<AttachmentFile | null>(null);
  const [previewTool, setPreviewTool] = useState<ToolEvent | null>(null);
  const [vncOpen, setVncOpen] = useState(false);
  const [restoringCheckpoint, setRestoringCheckpoint] = useState(false);
  const initialMessageSentRef = useRef(false);
  const chatInputRef = useRef<ChatInputRef>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevToolCountRef = useRef(0);

  const configEditable = session?.status === "pending" || session?.status === "completed";
  const timeline = useMemo(() => eventsToTimeline(events), [events]);
  const checkpointByAnchor = useMemo(() => {
    const map = new Map<string, SessionCheckpoint>();
    for (const checkpoint of checkpoints) {
      map.set(checkpoint.anchor_event_id, checkpoint);
    }
    return map;
  }, [checkpoints]);
  const planSteps = useMemo(() => getLatestPlanFromEvents(events), [events]);
  const observationSummary = useMemo(
    () => getTaskObservationSummary(events, session?.status),
    [events, session?.status],
  );
  const hasPreview = previewFile !== null || previewTool !== null;

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
          toast.error(e instanceof Error ? e.message : "发送消息失败");
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
      try {
        const attachmentIds = uploadedFiles.map((file) => file.id);
        await sendMessage(message, attachmentIds, {
          model_id: sessionModelId,
          skill_id: sessionSkillId,
          thinking_enabled: sessionThinkingEnabled,
        });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "发送失败，请重试");
        throw e;
      }
    },
    [sendMessage, sessionModelId, sessionSkillId, sessionThinkingEnabled],
  );

  const handleClarifyAnswer = useCallback(
    async (answer: string) => {
      await handleSend(answer, []);
    },
    [handleSend],
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
      toast.success("任务已停止");
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "停止任务失败");
    }
  }, [session, sessionId, refresh]);

  const handleDebugOpen = useCallback(() => {
    setIncludeDebug(true);
  }, []);

  const handleRestoreCheckpoint = useCallback(
    async (checkpoint: SessionCheckpoint) => {
      if (!session) return;
      const confirmed = window.confirm(
        `确定要回退到「${checkpoint.label || "此处"}」吗？\n\n将删除该点之后的所有对话、Agent 记忆、沙箱文件与 COS 文件记录。`,
      );
      if (!confirmed) return;

      setRestoringCheckpoint(true);
      try {
        if (session.status === "running") {
          await sessionApi.stopSession(sessionId);
        }
        await sessionApi.restoreCheckpoint(sessionId, checkpoint.id);
        toast.success("已回退到指定还原点");
        await refresh();
        await refreshFiles();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "回退失败");
      } finally {
        setRestoringCheckpoint(false);
      }
    },
    [session, sessionId, refresh, refreshFiles],
  );

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
    error,
    refresh,
    refreshFiles,
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
    handleDebugOpen,
    resolveCheckpoint,
    handleRestoreCheckpoint,
    restoringCheckpoint,
  };
}
