"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { sessionApi } from "@/lib/api/session";
import type {
  SessionDetail,
  SessionFile,
  SSEEventData,
  TokenUsageSummary,
  UpdateSessionConfigParams,
} from "@/lib/api/types";
import { normalizeEvent, normalizeEvents } from "@/lib/session-events";

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

export type UseSessionDetailResult = {
  session: SessionDetail | null;
  files: SessionFile[];
  events: SSEEventData[];
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
  refreshFiles: () => Promise<void>;
  sendMessage: (
    message: string,
    attachmentIds: string[],
    options?: { model_id?: string; skill_id?: string; thinking_enabled?: boolean },
  ) => Promise<void>;
  updateSessionConfig: (params: UpdateSessionConfigParams) => Promise<void>;
  streaming: boolean;
};

/**
 * 任务详情：拉取会话详情与文件列表，管理事件列表；
 * 未完成任务会通过 chat 空 body 流式拉取事件，发送消息时通过 chat 带 body 流式追加事件。
 */
export function useSessionDetail(
  sessionId: string | null,
  initialSkipEmptyStream?: boolean,
  includeDebug?: boolean,
): UseSessionDetailResult {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [events, setEvents] = useState<SSEEventData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [skipEmptyStream, setSkipEmptyStream] = useState(initialSkipEmptyStream || false);
  const emptyStreamCleanupRef = useRef<(() => void) | null>(null);
  const messageStreamCleanupRef = useRef<(() => void) | null>(null);
  const emptyStreamRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionMissingRef = useRef(false);
  const isSendMessageRef = useRef(false);
  const lastEventIdRef = useRef<string | null>(null);

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
  }, [clearEmptyStreamRetryTimer]);

  const handleSessionMissing = useCallback(
    (err: unknown) => {
      sessionMissingRef.current = true;
      stopEmptyStream();
      setSession(null);
      setError(err instanceof Error ? err : new Error("会话不存在"));
      setStreaming(false);
      isSendMessageRef.current = false;
    },
    [stopEmptyStream],
  );

  const appendEvent = useCallback((ev: SSEEventData) => {
    let evToAppend = ev;
    if (
      ev.data &&
      typeof ev.data === "object" &&
      ("event" in ev.data || "type" in ev.data) &&
      "data" in ev.data
    ) {
      const normalized = normalizeEvent(
        ev.data as { event?: string; type?: string; data?: unknown },
      );
      if (normalized) evToAppend = normalized;
    }

    const eventId = (evToAppend.data as { event_id?: string })?.event_id;
    if (eventId) lastEventIdRef.current = eventId;

    setEvents((prev) => {
      if (
        eventId &&
        prev.some((item) => (item.data as { event_id?: string })?.event_id === eventId)
      ) {
        return prev;
      }
      return [...prev, evToAppend];
    });

    // 更新会话标题
    if (
      evToAppend.type === "title" &&
      evToAppend.data &&
      typeof (evToAppend.data as { title?: string }).title === "string"
    ) {
      setSession((prev) =>
        prev ? { ...prev, title: (evToAppend.data as { title: string }).title } : null,
      );
    }

    // 服务端权威会话状态
    if (evToAppend.type === "session_status") {
      const status = (evToAppend.data as { status?: SessionDetail["status"] }).status;
      if (status) {
        setSession((prev) => (prev ? { ...prev, status } : null));
        if (status === "waiting" || status === "completed" || status === "cancelled") {
          setStreaming(false);
        }
      }
    }

    if (evToAppend.type === "usage") {
      const usage = evToAppend.data as TokenUsageSummary;
      setSession((prev) => (prev ? { ...prev, token_usage: usage } : null));
    }

    // done 事件时更新为 completed，并刷新 token 汇总（兜底）
    if (evToAppend.type === "done") {
      setSession((prev) => (prev ? { ...prev, status: "completed" } : null));
    }

    // error 事件时也可以认为任务结束
    if (evToAppend.type === "error") {
      if (getSessionMissingErrorFromEvent(evToAppend)) {
        sessionMissingRef.current = true;
      }
      setSession((prev) => (prev ? { ...prev, status: "completed" } : null));
    }
  }, []);

  const startEmptyStream = useCallback(() => {
    if (!sessionId || sessionMissingRef.current) return;
    stopEmptyStream();
    emptyStreamCleanupRef.current = sessionApi.chat(
      sessionId,
      { event_id: lastEventIdRef.current || undefined },
      (ev) => {
        appendEvent(ev);
        if (getSessionMissingErrorFromEvent(ev)) {
          handleSessionMissing(new Error("会话不存在"));
        }
      },
      (err) => {
        if (err.name === "AbortError") {
          return;
        }
        if (isSessionMissingError(err)) {
          emptyStreamCleanupRef.current = null;
          handleSessionMissing(err);
          return;
        }
        // 流正常结束（服务端关闭连接），延迟重连
        if (err.message === "SSE_STREAM_END") {
          emptyStreamCleanupRef.current = null;
          clearEmptyStreamRetryTimer();
          emptyStreamRetryTimerRef.current = setTimeout(() => {
            emptyStreamRetryTimerRef.current = null;
            if (sessionMissingRef.current) return;
            if (!emptyStreamCleanupRef.current && !isSendMessageRef.current) {
              startEmptyStream();
            }
          }, 500);
          return;
        }
        setError(err instanceof Error ? err : new Error("会话流连接异常"));
      },
      { include_debug: includeDebug },
    );
  }, [
    sessionId,
    appendEvent,
    stopEmptyStream,
    clearEmptyStreamRetryTimer,
    handleSessionMissing,
    includeDebug,
  ]);

  const normalizeFileList = useCallback((raw: unknown): SessionFile[] => {
    if (Array.isArray(raw)) return raw as SessionFile[];
    if (
      raw &&
      typeof raw === "object" &&
      "files" in raw &&
      Array.isArray((raw as { files: unknown }).files)
    ) {
      return (raw as { files: SessionFile[] }).files;
    }
    if (
      raw &&
      typeof raw === "object" &&
      "data" in raw &&
      Array.isArray((raw as { data: unknown }).data)
    ) {
      return (raw as { data: SessionFile[] }).data;
    }
    return [];
  }, []);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setError(null);
    try {
      const [detail, fileListRaw] = await Promise.all([
        sessionApi.getSessionDetail(sessionId, { include_debug: includeDebug, events_limit: 100 }),
        sessionApi.getSessionFiles(sessionId),
      ]);
      setSession(detail);
      setFiles(normalizeFileList(fileListRaw));
      const rawEvents = (detail as { events?: unknown }).events;
      const pagedEvents: SSEEventData[] = [];
      if (rawEvents && Array.isArray(rawEvents) && rawEvents.length > 0) {
        pagedEvents.push(...normalizeEvents(rawEvents));
      }

      let cursor = (detail as { events_next_cursor?: number | null }).events_next_cursor ?? null;
      while (cursor != null) {
        const page = await sessionApi.getSessionEvents(sessionId, {
          after: cursor,
          limit: 100,
          include_debug: includeDebug,
        });
        pagedEvents.push(...normalizeEvents((page as { events?: unknown }).events));
        cursor = (page as { next_cursor?: number | null }).next_cursor ?? null;
      }

      setEvents(pagedEvents);
      if (pagedEvents.length > 0) {
        const lastEvId = (pagedEvents[pagedEvents.length - 1]?.data as { event_id?: string })
          ?.event_id;
        if (lastEvId) lastEventIdRef.current = lastEvId;
      }
    } catch (e) {
      if (isSessionMissingError(e)) {
        handleSessionMissing(e);
      } else {
        setError(e instanceof Error ? e : new Error("加载失败"));
      }
    } finally {
      setLoading(false);
    }
  }, [sessionId, normalizeFileList, handleSessionMissing, includeDebug]);

  const refreshFiles = useCallback(async () => {
    if (!sessionId) return;
    try {
      const fileListRaw = await sessionApi.getSessionFiles(sessionId);
      setFiles(normalizeFileList(fileListRaw));
    } catch {
      setError(new Error("刷新文件列表失败"));
    }
  }, [sessionId, normalizeFileList]);

  useEffect(() => {
    if (!sessionId) {
      setLoading(false);
      setSession(null);
      setFiles([]);
      setEvents([]);
      setError(null);
      sessionMissingRef.current = false;
      stopEmptyStream();
      return;
    }
    sessionMissingRef.current = false;
    setLoading(true);
    refresh().then(() => {
      // 由下面的 effect 根据 session 状态决定是否开空流
    });
    return () => {
      stopEmptyStream();
    };
  }, [sessionId, refresh, stopEmptyStream]);

  const sessionStatus = session?.status;

  useEffect(() => {
    if (!sessionId || !sessionStatus || sessionMissingRef.current) return;
    const completed = sessionStatus === "completed" || sessionStatus === "cancelled";
    // 如果标记了跳过空流（比如有初始消息待发送），则不启动空流
    if (!completed && !isSendMessageRef.current && !skipEmptyStream) {
      startEmptyStream();
    }
    return () => {
      stopEmptyStream();
    };
  }, [sessionId, sessionStatus, skipEmptyStream, startEmptyStream, stopEmptyStream]);

  // 组件卸载时清理消息流
  useEffect(() => {
    return () => {
      clearEmptyStreamRetryTimer();
      if (messageStreamCleanupRef.current) {
        messageStreamCleanupRef.current();
        messageStreamCleanupRef.current = null;
      }
    };
  }, [clearEmptyStreamRetryTimer]);

  const updateSessionConfig = useCallback(
    async (params: UpdateSessionConfigParams) => {
      if (!sessionId) return;
      const updated = await sessionApi.updateSessionConfig(sessionId, params);
      setSession(updated);
    },
    [sessionId],
  );

  const sendMessage = useCallback(
    async (
      message: string,
      attachmentIds: string[],
      options?: { model_id?: string; skill_id?: string; thinking_enabled?: boolean },
    ) => {
      if (!sessionId) return;
      stopEmptyStream();
      // 清理已有的消息流连接（如 waiting 状态时用户再次发送）
      if (messageStreamCleanupRef.current) {
        messageStreamCleanupRef.current();
        messageStreamCleanupRef.current = null;
      }
      // 发送消息时，清除跳过空流的标记
      setSkipEmptyStream(false);
      isSendMessageRef.current = true;
      setStreaming(true);

      // 立即更新状态为 running，不等待 SSE 事件
      setSession((prev) => (prev ? { ...prev, status: "running" } : null));

      const onEvent = (ev: SSEEventData) => {
        appendEvent(ev);
        if (getSessionMissingErrorFromEvent(ev)) {
          if (messageStreamCleanupRef.current) {
            messageStreamCleanupRef.current();
            messageStreamCleanupRef.current = null;
          }
          handleSessionMissing(new Error("会话不存在"));
          return;
        }
        if (ev.type === "done") {
          setStreaming(false);
          isSendMessageRef.current = false;
          // 清理消息流的 cleanup
          if (messageStreamCleanupRef.current) {
            messageStreamCleanupRef.current();
            messageStreamCleanupRef.current = null;
          }
          setSession((prev) => (prev ? { ...prev } : null));
          startEmptyStream();
        }
      };
      const messageStreamCleanup = sessionApi.chat(
        sessionId,
        {
          message,
          attachments: attachmentIds,
          model_id: options?.model_id,
          skill_id: options?.skill_id,
          thinking_enabled: options?.thinking_enabled,
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
            handleSessionMissing(err);
            return;
          }
          // 流正常结束（服务端关闭连接），重置状态并启动空流监听后续事件
          if (err.message === "SSE_STREAM_END") {
            setStreaming(false);
            isSendMessageRef.current = false;
            if (messageStreamCleanupRef.current) {
              messageStreamCleanupRef.current();
              messageStreamCleanupRef.current = null;
            }
            startEmptyStream();
            return;
          }
          // 实际错误
          setError(err instanceof Error ? err : new Error("流式响应异常"));
          setStreaming(false);
          isSendMessageRef.current = false;
          setSession((prev) => (prev ? { ...prev, status: "completed" } : null));
          if (messageStreamCleanupRef.current) {
            messageStreamCleanupRef.current();
            messageStreamCleanupRef.current = null;
          }
          if (!sessionMissingRef.current) {
            startEmptyStream();
          }
        },
        { include_debug: includeDebug },
      );
      // 将消息流的 cleanup 存到独立的 ref，不与 emptyStream 混淆
      messageStreamCleanupRef.current = messageStreamCleanup;
    },
    [sessionId, appendEvent, startEmptyStream, stopEmptyStream, handleSessionMissing, includeDebug],
  );

  return {
    session,
    files,
    events,
    loading,
    error,
    refresh,
    refreshFiles,
    sendMessage,
    updateSessionConfig,
    streaming,
  };
}
