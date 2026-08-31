"use client";

import { type MutableRefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/api";
import { modelErrorMessage } from "@/lib/api/inference-errors";
import { sessionApi } from "@/lib/api/session";
import type { SessionDetail, SSEEventData } from "@/lib/api/types";
import { reduceSessionStatusState, type SessionStatusReductionState } from "@/lib/session-events";

function isSessionMissingError(err: unknown): boolean {
  if (err instanceof ApiError) {
    if (err.code === 404) return true;
    return (
      err.errorKey === "errors.sessionNotFound" || err.errorKey === "errors.taskSessionNotFound"
    );
  }
  return false;
}

function getSessionMissingErrorFromEvent(ev: SSEEventData): boolean {
  if (ev.type !== "error") return false;
  const errorMsg = (ev.data as { error?: string })?.error;
  return typeof errorMsg === "string" && isSessionMissingError(new Error(errorMsg));
}

function shouldMaintainEmptyStream(status?: SessionDetail["status"]): boolean {
  return status === "running";
}

type StreamDeps = {
  sessionId: string | null;
  sessionStatus?: SessionDetail["status"];
  appendEvent: (ev: SSEEventData) => boolean;
  onSessionMissing: (err: unknown) => void;
  applySessionPatch: (patch: Partial<SessionDetail>) => void;
  setError: (err: Error | null) => void;
  lastEventIdRef: MutableRefObject<string | null>;
  initialEventsLoaded?: boolean;
  skipEmptyStream?: boolean;
  onReconnect?: () => Promise<void>;
};

export type SessionStreamStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "stale"
  | "error";

export function useSessionStreams({
  sessionId,
  sessionStatus,
  appendEvent,
  onSessionMissing,
  applySessionPatch,
  setError,
  lastEventIdRef,
  initialEventsLoaded = false,
  skipEmptyStream = false,
  onReconnect,
}: StreamDeps) {
  const t = useTranslations("sessionDetail");
  const messages = useMemo(
    () => ({
      taskCancelledNotice: t("taskCancelledNotice"),
      taskFailedNotice: t("taskFailedNotice"),
      sessionNotFound: t("sessionNotFound"),
      streamError: t("streamError"),
      streamResponseError: t("streamResponseError"),
    }),
    [t],
  );
  const [streaming, setStreaming] = useState(false);
  const [streamStatus, setStreamStatus] = useState<SessionStreamStatus>("idle");
  const [streamError, setStreamError] = useState<Error | null>(null);
  const emptyStreamCleanupRef = useRef<(() => void) | null>(null);
  const messageStreamCleanupRef = useRef<(() => void) | null>(null);
  const emptyStreamRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const emptyStreamRetryCountRef = useRef(0);
  const sessionMissingRef = useRef(false);
  const isSendMessageRef = useRef(false);
  const startEmptyStreamRef = useRef<(() => void) | null>(null);
  const sessionStatusRef = useRef(sessionStatus);
  const sessionStatusStateRef = useRef<SessionStatusReductionState>({
    status: sessionStatus,
  });
  const dependenciesRef = useRef({
    appendEvent,
    onSessionMissing,
    applySessionPatch,
    setError,
    lastEventIdRef,
    onReconnect,
    messages,
  });

  useEffect(() => {
    dependenciesRef.current = {
      appendEvent,
      onSessionMissing,
      applySessionPatch,
      setError,
      lastEventIdRef,
      onReconnect,
      messages,
    };
  }, [
    appendEvent,
    onSessionMissing,
    applySessionPatch,
    setError,
    lastEventIdRef,
    onReconnect,
    messages,
  ]);

  useEffect(() => {
    sessionStatusRef.current = sessionStatus;
    sessionStatusStateRef.current.status = sessionStatus;
  }, [sessionStatus]);

  const clearEmptyStreamRetryTimer = useCallback(() => {
    if (emptyStreamRetryTimerRef.current) {
      clearTimeout(emptyStreamRetryTimerRef.current);
      emptyStreamRetryTimerRef.current = null;
    }
  }, []);

  const stopEmptyStream = useCallback(() => {
    clearEmptyStreamRetryTimer();
    if (emptyStreamCleanupRef.current) {
      emptyStreamCleanupRef.current();
      emptyStreamCleanupRef.current = null;
    }
    if (!messageStreamCleanupRef.current) {
      setStreamStatus("idle");
    }
  }, [clearEmptyStreamRetryTimer]);

  const handleStreamEvent = useCallback((ev: SSEEventData) => {
    const {
      appendEvent: appendLatestEvent,
      applySessionPatch: applyLatestSessionPatch,
      setError: setLatestError,
      messages: latestMessages,
    } = dependenciesRef.current;
    setStreamStatus("connected");
    setStreamError(null);
    const accepted = appendLatestEvent(ev);
    if (!accepted) return;

    if (ev.type === "session_status") {
      const state = reduceSessionStatusState([ev], sessionStatusStateRef.current);
      sessionStatusStateRef.current = state;
      const status = state.status;
      if (status) {
        sessionStatusRef.current = status;
        applyLatestSessionPatch({ status });
        if (
          status === "waiting" ||
          status === "completed" ||
          status === "cancelled" ||
          status === "failed"
        ) {
          setStreaming(false);
        }
        if (status === "cancelled") {
          setStreamError(new Error(latestMessages.taskCancelledNotice));
          setStreamStatus("error");
        }
        if (status === "failed") {
          setStreamError(new Error(latestMessages.taskFailedNotice));
          setStreamStatus("error");
        }
      }
    }

    if (ev.type === "done") {
      sessionStatusRef.current = "completed";
      sessionStatusStateRef.current.status = "completed";
      applyLatestSessionPatch({ status: "completed" });
      setStreaming(false);
    }

    if (ev.type === "error") {
      if (getSessionMissingErrorFromEvent(ev)) {
        sessionMissingRef.current = true;
      }
      const code = (ev.data as { code?: string | null })?.code;
      const friendly = modelErrorMessage(code);
      if (friendly) {
        setLatestError(new Error(friendly));
      }
      const retryable = (ev.data as { retryable?: boolean | null })?.retryable === true;
      if (retryable) {
        // The kernel auto-retries this attempt (RunAttemptFailed -> RunRetried).
        // Surface the transient error but keep the run live so the reconnecting
        // empty stream picks up the retried attempt instead of freezing the UI
        // on a dead "failed" state until a manual refresh.
        setStreamStatus("reconnecting");
        return;
      }
      applyLatestSessionPatch({ status: "failed" });
      setStreaming(false);
      setStreamStatus("error");
    }
  }, []);

  const startEmptyStream = useCallback(() => {
    if (!sessionId || sessionMissingRef.current) return;
    if (!shouldMaintainEmptyStream(sessionStatusRef.current)) return;
    if (emptyStreamCleanupRef.current || isSendMessageRef.current) return;
    clearEmptyStreamRetryTimer();
    setStreamStatus(emptyStreamRetryCountRef.current > 0 ? "reconnecting" : "connecting");
    const resumeEventId = dependenciesRef.current.lastEventIdRef.current || undefined;
    emptyStreamCleanupRef.current = sessionApi.chat(
      sessionId,
      { event_id: resumeEventId },
      (ev) => {
        emptyStreamRetryCountRef.current = 0;
        handleStreamEvent(ev);
        if (getSessionMissingErrorFromEvent(ev)) {
          const { onSessionMissing: handleSessionMissing, messages: latestMessages } =
            dependenciesRef.current;
          handleSessionMissing(new Error(latestMessages.sessionNotFound));
        }
      },
      (err) => {
        if (err.name === "AbortError") return;
        if (isSessionMissingError(err)) {
          emptyStreamCleanupRef.current = null;
          dependenciesRef.current.onSessionMissing(err);
          return;
        }
        if (err.message === "SSE_STREAM_END") {
          emptyStreamCleanupRef.current = null;
          clearEmptyStreamRetryTimer();
          const retryCount = emptyStreamRetryCountRef.current;
          const delay = Math.min(30_000, 1000 * 2 ** Math.min(retryCount, 5));
          emptyStreamRetryCountRef.current = retryCount + 1;
          setStreamStatus(retryCount >= 2 ? "stale" : "reconnecting");
          emptyStreamRetryTimerRef.current = setTimeout(() => {
            emptyStreamRetryTimerRef.current = null;
            if (sessionMissingRef.current) return;
            if (
              shouldMaintainEmptyStream(sessionStatusRef.current) &&
              !emptyStreamCleanupRef.current &&
              !isSendMessageRef.current
            ) {
              void (dependenciesRef.current.onReconnect?.() ?? Promise.resolve()).finally(() => {
                startEmptyStreamRef.current?.();
              });
            }
          }, delay);
          return;
        }
        const { setError: setLatestError, messages: latestMessages } = dependenciesRef.current;
        const nextError = err instanceof Error ? err : new Error(latestMessages.streamError);
        setStreamError(nextError);
        setStreamStatus("error");
        setLatestError(nextError);
      },
    );
  }, [sessionId, handleStreamEvent, clearEmptyStreamRetryTimer]);

  useEffect(() => {
    startEmptyStreamRef.current = startEmptyStream;
  });

  const sendMessage = useCallback(
    async (
      message: string,
      attachmentIds: string[],
      options?: {
        model_id?: string;
        skill_id?: string;
        thinking_enabled?: boolean;
        mode?: import("@/lib/api/types").SessionMode;
      },
    ) => {
      if (!sessionId) return;
      stopEmptyStream();
      if (messageStreamCleanupRef.current) {
        messageStreamCleanupRef.current();
        messageStreamCleanupRef.current = null;
      }
      isSendMessageRef.current = true;
      setStreaming(true);
      setStreamStatus("connecting");
      setStreamError(null);
      sessionStatusRef.current = "running";
      dependenciesRef.current.applySessionPatch({ status: "running" });

      const onEvent = (ev: SSEEventData) => {
        handleStreamEvent(ev);
        if (getSessionMissingErrorFromEvent(ev)) {
          if (messageStreamCleanupRef.current) {
            messageStreamCleanupRef.current();
            messageStreamCleanupRef.current = null;
          }
          const { onSessionMissing: handleSessionMissing, messages: latestMessages } =
            dependenciesRef.current;
          handleSessionMissing(new Error(latestMessages.sessionNotFound));
          return;
        }
        if (ev.type === "done") {
          setStreaming(false);
        }
      };

      messageStreamCleanupRef.current = sessionApi.chat(
        sessionId,
        {
          message,
          request_id: crypto.randomUUID(),
          attachments: attachmentIds,
          model_id: options?.model_id,
          skill_id: options?.skill_id,
          thinking_enabled: options?.thinking_enabled,
          mode: options?.mode,
        },
        onEvent,
        (err) => {
          if (err.name === "AbortError") {
            setStreaming(false);
            isSendMessageRef.current = false;
            return;
          }
          if (isSessionMissingError(err)) {
            if (messageStreamCleanupRef.current) {
              messageStreamCleanupRef.current();
              messageStreamCleanupRef.current = null;
            }
            dependenciesRef.current.onSessionMissing(err);
            return;
          }
          if (err.message === "SSE_STREAM_END") {
            setStreaming(false);
            isSendMessageRef.current = false;
            if (messageStreamCleanupRef.current) {
              messageStreamCleanupRef.current();
              messageStreamCleanupRef.current = null;
            }
            if (shouldMaintainEmptyStream(sessionStatusRef.current)) {
              startEmptyStream();
            }
            return;
          }
          const { setError: setLatestError, messages: latestMessages } = dependenciesRef.current;
          const nextError =
            err instanceof Error ? err : new Error(latestMessages.streamResponseError);
          setLatestError(nextError);
          setStreaming(false);
          isSendMessageRef.current = false;
          setStreamError(nextError);
          setStreamStatus("error");
          if (messageStreamCleanupRef.current) {
            messageStreamCleanupRef.current();
            messageStreamCleanupRef.current = null;
          }
          if (!sessionMissingRef.current) {
            if (shouldMaintainEmptyStream(sessionStatusRef.current)) {
              void (dependenciesRef.current.onReconnect?.() ?? Promise.resolve()).finally(() => {
                startEmptyStream();
              });
            }
          }
        },
      );
    },
    [sessionId, handleStreamEvent, startEmptyStream, stopEmptyStream],
  );

  const resumeAfterExternalCommand = useCallback(() => {
    if (!sessionId || sessionMissingRef.current) return;
    stopEmptyStream();
    if (messageStreamCleanupRef.current) {
      messageStreamCleanupRef.current();
      messageStreamCleanupRef.current = null;
    }
    isSendMessageRef.current = false;
    emptyStreamRetryCountRef.current = 0;
    sessionStatusRef.current = "running";
    sessionStatusStateRef.current.status = "running";
    dependenciesRef.current.applySessionPatch({ status: "running" });
    setStreaming(true);
    setStreamError(null);
    startEmptyStream();
  }, [sessionId, startEmptyStream, stopEmptyStream]);

  useEffect(() => {
    if (!sessionId || !sessionStatus || sessionMissingRef.current) return;
    if (
      initialEventsLoaded &&
      shouldMaintainEmptyStream(sessionStatus) &&
      !isSendMessageRef.current &&
      !skipEmptyStream
    ) {
      const timer = window.setTimeout(startEmptyStream, 0);
      return () => {
        window.clearTimeout(timer);
        stopEmptyStream();
      };
    }
    return () => {
      stopEmptyStream();
    };
  }, [
    sessionId,
    sessionStatus,
    initialEventsLoaded,
    skipEmptyStream,
    startEmptyStream,
    stopEmptyStream,
  ]);

  useEffect(() => {
    return () => {
      clearEmptyStreamRetryTimer();
      if (messageStreamCleanupRef.current) {
        messageStreamCleanupRef.current();
        messageStreamCleanupRef.current = null;
      }
    };
  }, [clearEmptyStreamRetryTimer]);

  const resetStreams = useCallback(() => {
    sessionMissingRef.current = false;
    isSendMessageRef.current = false;
    emptyStreamRetryCountRef.current = 0;
    stopEmptyStream();
    if (messageStreamCleanupRef.current) {
      messageStreamCleanupRef.current();
      messageStreamCleanupRef.current = null;
    }
    setStreaming(false);
    setStreamStatus("idle");
    setStreamError(null);
  }, [stopEmptyStream]);

  return {
    streaming,
    streamStatus,
    streamError,
    sendMessage,
    resumeAfterExternalCommand,
    resetStreams,
    markSessionMissing: () => {
      sessionMissingRef.current = true;
    },
  };
}
