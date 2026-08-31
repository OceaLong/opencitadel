"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  capabilitiesApi,
  type CapabilityName,
  type CapabilitySnapshot,
  type CapabilityState,
} from "@/lib/api/capabilities";
import { useAuth } from "@/providers/auth-provider";

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

  const capability = useCallback(
    (name: CapabilityName): CapabilityState | undefined => snapshot?.items[name],
    [snapshot],
  );

  return { snapshot, loading, reload, capability };
}
