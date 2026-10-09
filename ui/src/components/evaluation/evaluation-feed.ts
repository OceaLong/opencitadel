"use client";
import { useEffect } from "react";

import { evaluationApi } from "@/lib/api/evaluations";
import { ApiError } from "@/lib/api/fetch";

import type { EvaluationAccess } from "./evaluation-boundary";

/** Every reconnect invalidates metadata; the caller retains its explicit score revision. */
export function useEvaluationFeed(access: EvaluationAccess, batchId: string, refresh: () => void) {
  const { workspaceId, deny } = access;
  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const changed = () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => {
        if (!controller.signal.aborted) refresh();
      }, 500);
    };
    const connect = async () => {
      try {
        await evaluationApi.batchEvents(
          batchId,
          { workspaceId: workspaceId, signal: controller.signal },
          changed,
          (code) => {
            if (controller.signal.aborted) return;
            if (code === "permission_denied") {
              controller.abort();
              deny?.();
            } else changed();
          },
        );
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && [401, 403].includes(error.code)) {
          controller.abort();
          deny?.();
          return;
        }
      }
      if (!controller.signal.aborted) {
        refresh();
        retry = setTimeout(() => void connect(), 3000);
      }
    };
    const usagePoll = setInterval(() => {
      if (!controller.signal.aborted) refresh();
    }, 30000);
    void connect();
    return () => {
      controller.abort();
      clearTimeout(retry);
      clearTimeout(debounce);
      clearInterval(usagePoll);
    };
  }, [workspaceId, deny, batchId, refresh]);
}
