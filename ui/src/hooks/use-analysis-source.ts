"use client";
import { useEffect, useRef } from "react";

import { sourceIdentity } from "@/lib/analysis-view/source-identity";
import type { RequestOptions } from "@/lib/api/fetch";

type Source = Parameters<typeof sourceIdentity>[0];
/** Revalidate fixed facts on focus and while mounted; stale owners fail closed. */
export function useAnalysisSource({
  value,
  read,
  invalidate,
  workspaceId,
}: {
  value: Source | null;
  read: (options: RequestOptions) => Promise<Source>;
  invalidate: () => void;
  workspaceId?: string;
}) {
  const latest = useRef({ read, invalidate });
  useEffect(() => {
    latest.current = { read, invalidate };
  }, [read, invalidate]);
  const identity = value ? sourceIdentity(value) : null;
  useEffect(() => {
    if (identity === null) return;
    let active = true;
    let controller: AbortController | null = null;
    const check = async () => {
      controller?.abort();
      const current = new AbortController();
      controller = current;
      try {
        const fresh = await latest.current.read({ workspaceId, signal: current.signal });
        if (active && !current.signal.aborted && sourceIdentity(fresh) !== identity)
          latest.current.invalidate();
      } catch {
        if (active && !current.signal.aborted) latest.current.invalidate();
      }
    };
    const timer = window.setInterval(() => void check(), 30_000);
    const focus = () => void check();
    window.addEventListener("focus", focus);
    window.addEventListener("pageshow", focus);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", focus);
      window.removeEventListener("pageshow", focus);
    };
  }, [identity, workspaceId]);
}
