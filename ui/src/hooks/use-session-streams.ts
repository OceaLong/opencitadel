"use client";

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";

import { ApiError } from "@/lib/api";
import { sessionApi } from "@/lib/api/session";
import type { ClarifyAnswer, SessionDetail, SSEEventData, TokenUsageSummary } from "@/lib/api/types";

function isSessionMissingError(err: unknown): boolean {
  if (err instanceof ApiError && err.code === 404) {
    return true;
  }
  const msg = err instanceof Error ? err.message : String(err);
  return msg.includes("会话不存在") || msg.includes("任务会话不存在");
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
  appendEvent: (ev: SSEEventData) => void;
  onSessionMissing: (err: unknown) => void;
  applySessionPatch: (patch: Partial<SessionDetail>) => void;
  setError: (err: Error | null) => void;
  lastEventIdRef: MutableRefObject<string | null>;
  initialEventsLoaded?: boolean;
  skipEmptyStream?: boolean;
  onReconnect?: () => Promise<void>;
};

export type SessionStreamStatus = "idle" | "connecting" | "connected" | "reconnecting" | "stale" | "error";

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
  const [streaming, setStreaming] = useState(false);
  const [streamStatus, setStreamStatus] = useState<SessionStreamStatus>("idle");
  const [streamError, setStreamError] = useState<Error | null>(null);
  const streamIncludeDebugRef = useRef(false);
  const emptyStreamCleanupRef = useRef<(() => void) | null>(null);
  const messageStreamCleanupRef = useRef<(() => void) | null>(null);
  const emptyStreamRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const emptyStreamRetryCountRef = useRef(0);
  const sessionMissingRef = useRef(false);
  const isSendMessageRef = useRef(false);
  const startEmptyStreamRef = useRef<(() => void) | null>(null);
  const sessionStatusRef = useRef(sessionStatus);

  useEffect(() => {
    sessionStatusRef.current = sessionStatus;
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

  const handleStreamEvent = useCallback(
    (ev: SSEEventData) => {
      setStreamStatus("connected");
      setStreamError(null);
      appendEvent(ev);

      if (
        ev.type === "title" &&
        ev.data &&
        typeof (ev.data as { title?: string }).title === "string"
      ) {
        applySessionPatch({ title: (ev.data as { title: string }).title });
      }

      if (ev.type === "session_status") {
        const status = (ev.data as { status?: SessionDetail["status"] }).status;
        if (status) {
          sessionStatusRef.current = status;
          applySessionPatch({ status });
          if (
            status === "waiting" ||
            status === "completed" ||
            status === "cancelled" ||
            status === "failed"
          ) {
            setStreaming(false);
          }
        }
      }

      if (ev.type === "usage") {
        applySessionPatch({ token_usage: ev.data as TokenUsageSummary });
      }

      if (ev.type === "done") {
        setStreaming(false);
      }

      if (ev.type === "error") {
        if (getSessionMissingErrorFromEvent(ev)) {
          sessionMissingRef.current = true;
        }
        applySessionPatch({ status: "failed" });
        setStreaming(false);
        setStreamStatus("error");
      }
    },
    [appendEvent, applySessionPatch],
  );

  const startEmptyStream = useCallback(() => {
    if (!sessionId || sessionMissingRef.current) return;
    if (!shouldMaintainEmptyStream(sessionStatusRef.current)) return;
    stopEmptyStream();
    setStreamStatus(emptyStreamRetryCountRef.current > 0 ? "reconnecting" : "connecting");
    emptyStreamCleanupRef.current = sessionApi.chat(
      sessionId,
      { event_id: lastEventIdRef.current || undefined },
      (ev) => {
        emptyStreamRetryCountRef.current = 0;
        handleStreamEvent(ev);
        if (getSessionMissingErrorFromEvent(ev)) {
          onSessionMissing(new Error("会话不存在"));
        }
      },
      (err) => {
        if (err.name === "AbortError") return;
        if (isSessionMissingError(err)) {
          emptyStreamCleanupRef.current = null;
          onSessionMissing(err);
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
              void (onReconnect?.() ?? Promise.resolve()).finally(() => {
                startEmptyStreamRef.current?.();
              });
            }
          }, delay);
          return;
        }
        const nextError = err instanceof Error ? err : new Error("会话流连接异常");
        setStreamError(nextError);
        setStreamStatus("error");
        setError(nextError);
      },
      { include_debug: streamIncludeDebugRef.current },
    );
  }, [
    sessionId,
    handleStreamEvent,
    stopEmptyStream,
    clearEmptyStreamRetryTimer,
    onSessionMissing,
    setError,
    lastEventIdRef,
    onReconnect,
  ]);

  useEffect(() => {
    startEmptyStreamRef.current = startEmptyStream;
  });

  const enableDebugStream = useCallback(() => {
    streamIncludeDebugRef.current = true;
    if (!isSendMessageRef.current) {
      startEmptyStreamRef.current?.();
    }
  }, []);

  const sendMessage = useCallback(
    async (
      message: string,
      attachmentIds: string[],
      options?: {
        model_id?: string;
        skill_id?: string;
        thinking_enabled?: boolean;
        mode?: import("@/lib/api/types").SessionMode;
        clarify_answers?: ClarifyAnswer[];
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
      applySessionPatch({ status: "running" });

      const onEvent = (ev: SSEEventData) => {
        handleStreamEvent(ev);
        if (getSessionMissingErrorFromEvent(ev)) {
          if (messageStreamCleanupRef.current) {
            messageStreamCleanupRef.current();
            messageStreamCleanupRef.current = null;
          }
          onSessionMissing(new Error("会话不存在"));
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
          attachments: attachmentIds,
          model_id: options?.model_id,
          skill_id: options?.skill_id,
          thinking_enabled: options?.thinking_enabled,
          mode: options?.mode,
          clarify_answers: options?.clarify_answers,
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
            onSessionMissing(err);
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
          const nextError = err instanceof Error ? err : new Error("流式响应异常");
          setError(nextError);
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
              void (onReconnect?.() ?? Promise.resolve()).finally(() => {
                startEmptyStream();
              });
            }
          }
        },
        { include_debug: streamIncludeDebugRef.current },
      );
    },
    [
      sessionId,
      handleStreamEvent,
      startEmptyStream,
      stopEmptyStream,
      onSessionMissing,
      setError,
      applySessionPatch,
      onReconnect,
    ],
  );

  useEffect(() => {
    if (!sessionId || !sessionStatus || sessionMissingRef.current) return;
    if (
      initialEventsLoaded &&
      shouldMaintainEmptyStream(sessionStatus) &&
      !isSendMessageRef.current &&
      !skipEmptyStream
    ) {
      startEmptyStream();
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
    streamIncludeDebugRef.current = false;
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
    enableDebugStream,
    resetStreams,
    markSessionMissing: () => {
      sessionMissingRef.current = true;
    },
  };
}
