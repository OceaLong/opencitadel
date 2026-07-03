"use client";

import { useCallback, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";

import { useSessionEventLog } from "@/hooks/use-session-event-log";
import { useSessionMeta } from "@/hooks/use-session-meta";
import { useSessionStreams, type SessionStreamStatus } from "@/hooks/use-session-streams";
import type {
  ClarifyAnswer,
  SessionCheckpoint,
  SessionDetail,
  SessionFile,
  SSEEventData,
  UpdateSessionConfigParams,
} from "@/lib/api/types";

export type UseSessionDetailResult = {
  session: SessionDetail | null;
  files: SessionFile[];
  events: SSEEventData[];
  checkpoints: SessionCheckpoint[];
  loading: boolean;
  loadingEarlier: boolean;
  hasEarlierHistory: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
  loadEarlierEvents: () => Promise<void>;
  refreshFiles: () => Promise<void>;
  sendMessage: (
    message: string,
    attachmentIds: string[],
    options?: {
      model_id?: string;
      skill_id?: string;
      thinking_enabled?: boolean;
      mode?: import("@/lib/api/types").SessionMode;
      clarify_answers?: ClarifyAnswer[];
    },
  ) => Promise<void>;
  updateSessionConfig: (params: UpdateSessionConfigParams) => Promise<void>;
  streaming: boolean;
  streamStatus: SessionStreamStatus;
  streamError: Error | null;
  enableDebugStream: () => void;
};

/**
 * 任务详情：组合 meta / event log / streams 子 hook。
 */
export function useSessionDetail(
  sessionId: string | null,
  initialSkipEmptyStream?: boolean,
): UseSessionDetailResult {
  const t = useTranslations("sessionDetail");
  const resetStreamsRef = useRef<(() => void) | null>(null);
  const markSessionMissingRef = useRef<(() => void) | null>(null);
  const setErrorRef = useRef<((err: Error | null) => void) | null>(null);
  const resetMetaRef = useRef<(() => void) | null>(null);

  const {
    session,
    files,
    checkpoints,
    loading,
    error,
    setError,
    refreshMeta,
    refreshFiles,
    updateSessionConfig,
    applySessionPatch,
    resetMeta,
  } = useSessionMeta(sessionId, (err) => {
    markSessionMissingRef.current?.();
    resetStreamsRef.current?.();
    resetMetaRef.current?.();
    setErrorRef.current?.(err instanceof Error ? err : new Error(t("sessionNotFound")));
  });

  const {
    events,
    appendEvent,
    loadEventsPage,
    syncMissingEvents,
    loadEarlierEvents: loadEarlier,
    loadingEarlier,
    hasEarlierHistory,
    initialEventsLoaded,
    lastEventIdRef,
    lastPersistedSeqRef,
    resetEvents,
  } = useSessionEventLog(sessionId);

  const refresh = useCallback(async () => {
    await Promise.all([refreshMeta(), loadEventsPage(false)]);
  }, [refreshMeta, loadEventsPage]);

  const streamIncludeDebugRef = useRef(false);

  const reconnect = useCallback(async () => {
    await refreshMeta();
    try {
      await syncMissingEvents(streamIncludeDebugRef.current);
    } catch {
      await loadEventsPage(false);
    }
  }, [refreshMeta, syncMissingEvents, loadEventsPage]);

  const streams = useSessionStreams({
    sessionId,
    sessionStatus: session?.status,
    appendEvent,
    onSessionMissing: (err) => {
      markSessionMissingRef.current?.();
      resetStreamsRef.current?.();
      resetMetaRef.current?.();
      setError(err instanceof Error ? err : new Error(t("sessionNotFound")));
    },
    applySessionPatch,
    setError,
    lastEventIdRef,
    lastPersistedSeqRef,
    initialEventsLoaded,
    skipEmptyStream: initialSkipEmptyStream,
    onReconnect: reconnect,
    onDebugModeChange: (enabled) => {
      streamIncludeDebugRef.current = enabled;
    },
  });

  useEffect(() => {
    setErrorRef.current = setError;
    resetMetaRef.current = resetMeta;
    resetStreamsRef.current = streams.resetStreams;
    markSessionMissingRef.current = streams.markSessionMissing;
  });

  const loadEarlierEvents = useCallback(async () => {
    await loadEarlier(false);
  }, [loadEarlier]);

  useEffect(() => {
    if (!sessionId) {
      resetMeta();
      resetEvents();
      streams.resetStreams();
      return;
    }
    resetEvents();
    void refresh();
    return () => {
      streams.resetStreams();
    };
  }, [sessionId]);

  return {
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
    sendMessage: streams.sendMessage,
    updateSessionConfig,
    streaming: streams.streaming,
    streamStatus: streams.streamStatus,
    streamError: streams.streamError,
    enableDebugStream: streams.enableDebugStream,
  };
}
