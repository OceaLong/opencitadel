"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslations } from "next-intl";

import type { Session } from "@/lib/api";
import { sessionApi } from "@/lib/api";
import { useAuth } from "@/providers/auth-provider";

/** 重连配置 */
const RETRY_CONFIG = {
  maxRetries: 10,
  baseDelay: 1000,
  maxDelay: 30_000,
} as const;

// ==================== Context ====================

type SessionsContextValue = {
  sessions: Session[];
  loading: boolean;
  error: string | null;
  /** 手动刷新（通过 REST 接口拉取一次） */
  refresh: () => Promise<void>;
  deleteSession: (sessionId: string) => Promise<{ success: boolean; message?: string }>;
  /** 当前搜索关键词（经 `q` 传给列表/流接口），空串表示不过滤 */
  query: string;
  /** 更新搜索关键词；调用方需自行 debounce（见 SessionList 搜索框） */
  setQuery: (query: string) => void;
};

const SessionsContext = createContext<SessionsContextValue | null>(null);

// ==================== Provider ====================

/**
 * 会话列表数据 Provider
 *
 * 无条件挂载在 `AppShell` 顶层（见 `src/components/app-shell.tsx`），挂载位置
 * 与激活模块无关，因此切换模块（进出 chat）不会卸载重挂本 Provider，
 * 会话列表 SSE 长连接得以常驻、不再来回断连重拉。
 *
 * `enabled`（默认 true）控制是否**发起**数据流，用于避免用户从未进入 chat
 * 模块时的无谓请求：
 *  - `enabled=false` 且从未激活过：不 fetch、不建立 SSE。
 *  - `enabled=true`：立即拉起 REST + SSE。
 *  - 一旦激活过（曾经 enabled=true），即保持流常驻——之后 `enabled` 再变
 *    false 也不会断开 SSE，优先避免来回切模块导致的重连风暴。
 *
 * 数据流:
 *  1. 首次激活后通过 REST GET /sessions 获取初始数据（仅一次）
 *  2. 同时建立 SSE POST /sessions/stream 长连接，接收实时推送
 *  3. SSE 断开后自动指数退避重连
 *  4. refresh() 可手动通过 REST 拉取
 */
export function SessionsProvider({
  children,
  enabled = true,
}: {
  children: React.ReactNode;
  enabled?: boolean;
}) {
  const t = useTranslations("sessions");
  const { user, loading: authLoading } = useAuth();
  const userId = user?.id ?? null;
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** 搜索关键词（已在 UI 侧 debounce），经 `q` 传给 REST + SSE。 */
  const [query, setQuery] = useState("");
  const queryRef = useRef(query);
  useEffect(() => {
    queryRef.current = query;
  }, [query]);
  const messages = useMemo(
    () => ({
      fetchFailed: t("fetchFailed"),
      streamDisconnected: t("streamDisconnected"),
    }),
    [t],
  );

  const cleanupRef = useRef<(() => void) | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 确保 REST 初始请求只发起一次（防止 Strict Mode 重复） */
  const initialFetchedRef = useRef(false);
  /** 标记 SSE 是否已经推送过数据，防止 REST 回调覆盖更新的 SSE 数据 */
  const sseReceivedRef = useRef(false);
  const messagesRef = useRef(messages);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 一旦进入过 chat 模块（enabled=true）即拉起并保持会话流常驻，
  // 之后 enabled 变回 false 也不断开，避免来回切换模块导致 SSE 反复重连。
  const [streamStarted, setStreamStarted] = useState(enabled);
  useEffect(() => {
    if (enabled && !streamStarted) setStreamStarted(true);
  }, [enabled, streamStarted]);

  // ---------- 手动刷新 ----------
  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await sessionApi.getSessions(queryRef.current);
      setSessions(data.sessions);
    } catch (err) {
      setError(err instanceof Error ? err.message : messagesRef.current.fetchFailed);
    } finally {
      setLoading(false);
    }
  }, []);

  // ---------- 初始 REST 请求（仅一次，登录后且流已激活） ----------
  useEffect(() => {
    if (authLoading) return;
    if (!streamStarted) return;
    if (!userId) {
      setSessions([]);
      setLoading(false);
      setError(null);
      initialFetchedRef.current = false;
      return;
    }
    if (initialFetchedRef.current) return;
    initialFetchedRef.current = true;

    sessionApi
      .getSessions(queryRef.current)
      .then((data) => {
        // 仅在 SSE 尚未推送过数据时更新，防止用旧数据覆盖 SSE 已推送的新数据
        if (!sseReceivedRef.current) {
          setSessions(data.sessions);
        }
        setLoading(false);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : messagesRef.current.fetchFailed);
        setLoading(false);
      });
  }, [authLoading, userId, streamStarted]);

  // ---------- 搜索关键词变化时 REST 重新拉取（跳过首帧，避免与初始请求重复） ----------
  // SSE 也会随 query 变化重连并推送过滤结果，但 REST 立即回填以获得更快的反馈。
  const querySettledRef = useRef(false);
  useEffect(() => {
    if (!querySettledRef.current) {
      querySettledRef.current = true;
      return;
    }
    if (authLoading || !userId || !streamStarted) return;

    let cancelled = false;
    setLoading(true);
    // 允许本次 REST 结果覆盖 UI（新的关键词对应一套全新列表）。
    sseReceivedRef.current = false;
    sessionApi
      .getSessions(query)
      .then((data) => {
        if (cancelled) return;
        if (!sseReceivedRef.current) {
          setSessions(data.sessions);
        }
        setLoading(false);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : messagesRef.current.fetchFailed);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [query, authLoading, userId, streamStarted]);

  // ---------- SSE 实时订阅 ----------
  useEffect(() => {
    if (authLoading || !userId || !streamStarted) return;

    let mounted = true;
    let retryCount = 0;

    const connect = () => {
      if (!mounted) return;

      // 清理上一次连接
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }

      const cleanup = sessionApi.streamSessions(
        // onSessions
        (newSessions) => {
          if (!mounted) return;
          retryCount = 0;
          sseReceivedRef.current = true;
          setSessions((prev) => {
            if (
              prev.length === newSessions.length &&
              prev.every(
                (item, index) =>
                  item.session_id === newSessions[index]?.session_id &&
                  item.latest_message_at === newSessions[index]?.latest_message_at &&
                  item.status === newSessions[index]?.status &&
                  item.unread_message_count === newSessions[index]?.unread_message_count,
              )
            ) {
              return prev;
            }
            return newSessions;
          });
          setLoading(false);
          setError(null);
        },
        // onError / onEnd
        () => {
          if (!mounted) return;

          if (retryCount >= RETRY_CONFIG.maxRetries) {
            setError(messagesRef.current.streamDisconnected);
            return;
          }

          const delay = Math.min(
            RETRY_CONFIG.baseDelay * Math.pow(2, retryCount),
            RETRY_CONFIG.maxDelay,
          );
          retryCount++;
          retryTimerRef.current = setTimeout(connect, delay);
        },
        // 关键词随 effect 依赖 query 变化而重连，携带最新过滤条件
        query,
      );

      cleanupRef.current = cleanup;
    };

    connect();

    return () => {
      mounted = false;
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [authLoading, userId, streamStarted, query]);

  // ---------- 删除会话 ----------
  const deleteSession = useCallback(
    async (sessionId: string): Promise<{ success: boolean; message?: string }> => {
      try {
        await sessionApi.deleteSession(sessionId);
        setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
        return { success: true };
      } catch (err) {
        // 把服务端的具体原因（如"会话仍有活动 Run，请先停止并等待进入终态"）
        // 带回给调用方展示，而不是吞成笼统的"删除失败，请重试"。
        return {
          success: false,
          message: err instanceof Error && err.message ? err.message : undefined,
        };
      }
    },
    [],
  );

  const contextValue = useMemo(
    () => ({ sessions, loading, error, refresh, deleteSession, query, setQuery }),
    [sessions, loading, error, refresh, deleteSession, query],
  );

  return <SessionsContext.Provider value={contextValue}>{children}</SessionsContext.Provider>;
}

// ==================== Hook ====================

/**
 * 获取会话列表数据的 Hook
 *
 * 必须在 <SessionsProvider> 内使用
 */
export function useSessions(): SessionsContextValue {
  const t = useTranslations("sessions");
  const ctx = useContext(SessionsContext);
  if (!ctx) {
    throw new Error(t("hookError"));
  }
  return ctx;
}
