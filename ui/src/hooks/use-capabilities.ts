"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  capabilitiesApi,
  type CapabilityName,
  type CapabilitySnapshot,
  type CapabilityState,
} from "@/lib/api/capabilities";
import { CAPABILITIES_CHANGED_EVENT, subscribeAppEvent } from "@/lib/events";
import { useAuth } from "@/providers/auth-provider";

/** 兜底轮询间隔（毫秒）：事件/焦点刷新失效时的最长陈旧窗口。 */
const POLL_INTERVAL_MS = 60_000;

export function useCapabilities() {
  const { user, loading: authLoading } = useAuth();
  const userId = user?.id ?? null;
  const authRef = useRef({ loading: authLoading, userId });
  authRef.current = { loading: authLoading, userId };
  const [snapshot, setSnapshot] = useState<CapabilitySnapshot | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const requestedUserId = authRef.current.userId;
    if (authRef.current.loading || !requestedUserId) {
      setSnapshot(null);
      setLoading(authRef.current.loading);
      return;
    }
    setLoading(true);
    try {
      const nextSnapshot = await capabilitiesApi.get();
      if (authRef.current.userId === requestedUserId && !authRef.current.loading) {
        setSnapshot(nextSnapshot);
      }
    } catch {
      if (authRef.current.userId === requestedUserId && !authRef.current.loading) {
        setSnapshot(null);
      }
    } finally {
      if (authRef.current.userId === requestedUserId && !authRef.current.loading) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (authLoading || !userId) {
      setSnapshot(null);
      setLoading(authLoading);
      return;
    }
    void reload();
  }, [authLoading, reload, userId]);

  // 及时性：窗口重获焦点 / 推理配置保存成功（CAPABILITIES_CHANGED_EVENT）时
  // 立即重拉；另有 60s 轮询兜底。卸载与登出时全部清理。
  useEffect(() => {
    if (authLoading || !userId) return;
    const onRefresh = () => void reload();
    window.addEventListener("focus", onRefresh);
    const unsubscribe = subscribeAppEvent(CAPABILITIES_CHANGED_EVENT, onRefresh);
    const timer = window.setInterval(onRefresh, POLL_INTERVAL_MS);
    return () => {
      window.removeEventListener("focus", onRefresh);
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [authLoading, reload, userId]);

  const capability = useCallback(
    (name: CapabilityName): CapabilityState | undefined => snapshot?.items[name],
    [snapshot],
  );

  return { snapshot, loading, reload, capability };
}
