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
import { useClientDataScope } from "@/providers/client-data-provider";

const POLL_INTERVAL_MS = 60_000;
export function useCapabilities() {
  const { user, loading: authLoading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const enabled = !authLoading && !!user && scope?.userId === user.id;
  const key = enabled ? JSON.stringify([user.id, scope?.workspaceId, scopeRevision]) : "";
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const [state, setState] = useState<{
    key: string;
    snapshot: CapabilitySnapshot | null;
    loading: boolean;
  }>({ key: "", snapshot: null, loading: true });
  const workspaceId = scope?.workspaceId;
  const reload = useCallback(async () => {
    controller.current?.abort();
    const request = ++generation.current;
    if (!key) return;
    const abort = new AbortController();
    controller.current = abort;
    const active = () => generation.current === request && !abort.signal.aborted;
    setState((previous) => ({
      key,
      snapshot: previous.key === key ? previous.snapshot : null,
      loading: true,
    }));
    try {
      const snapshot = await capabilitiesApi.get({ workspaceId, signal: abort.signal });
      if (active()) setState({ key, snapshot, loading: false });
    } catch {
      if (active()) setState({ key, snapshot: null, loading: false });
    }
  }, [key, workspaceId]);
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void reload();
    });
    return () => {
      cancelled = true;
      controller.current?.abort();
    };
  }, [reload]);
  useEffect(() => {
    if (!key) return;
    const refresh = () => void reload();
    window.addEventListener("focus", refresh);
    window.addEventListener("pageshow", refresh);
    const unsubscribe = subscribeAppEvent(CAPABILITIES_CHANGED_EVENT, refresh);
    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pageshow", refresh);
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [key, reload]);
  const snapshot = key && state.key === key ? state.snapshot : null;
  const loading = authLoading || (!!key && (state.key !== key || state.loading));
  const capability = useCallback(
    (name: CapabilityName): CapabilityState | undefined => snapshot?.items[name],
    [snapshot],
  );
  return { snapshot, loading, reload, capability };
}
