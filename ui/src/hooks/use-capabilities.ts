"use client";

import { useCallback, useEffect, useState } from "react";

import {
  capabilitiesApi,
  type CapabilityName,
  type CapabilitySnapshot,
  type CapabilityState,
} from "@/lib/api/capabilities";

export function useCapabilities() {
  const [snapshot, setSnapshot] = useState<CapabilitySnapshot | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setSnapshot(await capabilitiesApi.get());
    } catch {
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const capability = useCallback(
    (name: CapabilityName): CapabilityState | undefined => snapshot?.items[name],
    [snapshot],
  );

  return { snapshot, loading, reload, capability };
}
