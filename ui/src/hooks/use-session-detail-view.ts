"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import type { ChatInputRef } from "@/components/session/chat-input";

import { useIncrementalTimeline } from "@/hooks/use-incremental-timeline";
import { useRequireAuth } from "@/hooks/use-require-auth";
import { useSessionDetail } from "@/hooks/use-session-detail";
import { artifactsApi } from "@/lib/api/artifacts";
import { sessionApi } from "@/lib/api/session";
import type {
  ApprovalEventData,
  ArtifactEventSummary,
  FileInfo,
  SessionMode,
  Skill,
  SSEEventData,
  ToolEvent,
} from "@/lib/api/types";
import type { AttachmentFile, TimelineItem } from "@/lib/session-events";
import { getTaskObservationSummary } from "@/lib/session-events";

import type { Locale } from "@/i18n/routing";

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
    if (item.kind === "tool") {
      return item.data;
    }
  }
  return null;
}

function getLatestApprovalFromEvents(
  events: SSEEventData[],
  waiting: boolean,
): ApprovalEventData | null {
  if (!waiting) return null;
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (ev.type === "approval" && ev.data.options.length > 0) return ev.data;
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
  const detail = useSessionDetail(sessionId, hasInitialMessage);
  const {
    session,
    files,
    events,
    loading,
    loadingEarlier,
    hasEarlierHistory,
    error,
    refresh,
    loadEarlierEvents,
    refreshFiles,
    sendMessage,
    resumeAfterExternalCommand,
    updateSessionConfig,
    streaming,
    streamStatus,
    streamError,
  } = detail;

  const [activeSkill, setActiveSkill] = useState<Skill | null>(null);
  const [fileListOpen, setFileListOpen] = useState(false);
  const [previewFile, setPreviewFile] = useState<AttachmentFile | null>(null);
  const [previewTool, setPreviewTool] = useState<ToolEvent | null>(null);
  const [sessionArtifacts, setSessionArtifacts] = useState<ArtifactEventSummary[]>([]);
  const [dismissedArtifactsKey, setDismissedArtifactsKey] = useState<string | null>(null);
  const [vncOpen, setVncOpen] = useState(false);
  const initialMessageSentRef = useRef(false);
  const chatInputRef = useRef<ChatInputRef>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevToolCountRef = useRef(0);

  const configEditable =
    session?.status === "pending" ||
    session?.status === "completed" ||
    session?.status === "failed";
  const timeline = useIncrementalTimeline(events, locale);
  const sessionArtifactsKey = useMemo(
    () => sessionArtifacts.map((item) => `${item.artifact_id}:${item.version}`).join("|"),
    [sessionArtifacts],
  );
  const artifactsPreviewDismissed =
    sessionArtifactsKey !== "" && dismissedArtifactsKey === sessionArtifactsKey;
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

  // Artifacts only change when a build activity reports or the run completes.
  // Keying the refetch effect on `events.length` re-fetched artifacts once per
  // streamed SSE event (e.g. 300 events => 300 GETs); this signal advances only
  // on `resource_build`/`done` events so the effect fires event-driven instead.
  const artifactBuildSignal = useMemo(() => {
    let signal = 0;
    for (const ev of events) {
      if (ev.type === "resource_build" || ev.type === "done") signal += 1;
    }
    return signal;
  }, [events]);

  useEffect(() => {
    let cancelled = false;
    void artifactsApi
      .listBySession(sessionId)
      .then(({ artifacts }) => {
        if (cancelled) return;
        setSessionArtifacts(
          artifacts.map((artifact) => ({
            artifact_id: artifact.id,
            kind: artifact.kind,
            title: artifact.title,
            status: artifact.status,
            storage_ref: artifact.storage_ref,
            version: artifact.version_refs.length,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setSessionArtifacts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, artifactBuildSignal, session?.status]);

  const resolvedPreviewTool = useMemo(() => {
    if (!previewTool) return null;
    const id = (previewTool as { tool_call_id?: string }).tool_call_id;
    if (!id) return previewTool;

    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i];
      if (item.kind === "tool" && (item.data as { tool_call_id?: string }).tool_call_id === id) {
        return item.data;
      }
    }
    return previewTool;
  }, [previewTool, timeline]);

  useEffect(() => {
    if (session?.status !== "running" || vncOpen) return;

    const latestTool = findLatestTool(timeline);
    const toolCount = timeline.reduce((count, item) => {
      if (item.kind === "tool") return count + 1;
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
    t,
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
    [
      sendMessage,
      sessionModelId,
      sessionSkillId,
      sessionThinkingEnabled,
      mode,
      requireAuth,
      t,
      tAuth,
    ],
  );

  const handleApprovalSend = useCallback(
    async (message: string) => {
      if (!requireAuth(tAuth("loginToSendMessage"))) return;
      if (!latestApproval) throw new Error("Approval is no longer pending");
      const rejected = message.startsWith("reject") || message === "skip";
      const feedback =
        rejected && message.includes(":") ? message.slice(message.indexOf(":") + 1).trim() : "";
      await sessionApi.decideApproval(
        latestApproval.approval_id,
        rejected ? "rejected" : "approved",
        feedback,
      );
      resumeAfterExternalCommand();
    },
    [latestApproval, requireAuth, resumeAfterExternalCommand, tAuth],
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
    setPreviewTool(tool);
    setPreviewFile(null);
  }, []);

  const handleClosePreview = useCallback(() => {
    setPreviewFile(null);
    setPreviewTool(null);
    setDismissedArtifactsKey(sessionArtifactsKey);
  }, [sessionArtifactsKey]);

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
    handleApprovalSend,
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
  };
}
