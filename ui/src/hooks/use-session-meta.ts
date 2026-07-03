"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/api";
import { sessionApi } from "@/lib/api/session";
import type {
  SessionCheckpoint,
  SessionDetail,
  SessionFile,
  UpdateSessionConfigParams,
} from "@/lib/api/types";

function isSessionMissingError(err: unknown): boolean {
  if (err instanceof ApiError && err.code === 404) {
    return true;
  }
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("会话不存在") ||
    msg.includes("任务会话不存在") ||
    msg.includes("Session not found") ||
    msg.includes("Task not found")
  );
}

export function useSessionMeta(
  sessionId: string | null,
  onSessionMissing: (err: unknown) => void,
) {
  const t = useTranslations("sessionDetail");
  const tCommon = useTranslations("common");
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [checkpoints, setCheckpoints] = useState<SessionCheckpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

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

  const refreshMeta = useCallback(async () => {
    if (!sessionId) return;
    setError(null);
    try {
      const [detail, fileListRaw, checkpointData] = await Promise.all([
        sessionApi.getSessionDetail(sessionId, { include_debug: false, events_limit: 1 }),
        sessionApi.getSessionFiles(sessionId),
        sessionApi.listCheckpoints(sessionId).catch(() => ({ checkpoints: [] })),
      ]);
      setCheckpoints(checkpointData.checkpoints ?? []);
      const normalizedDetail =
        (detail.unread_message_count ?? 0) > 0
          ? { ...detail, unread_message_count: 0 }
          : detail;
      setSession(normalizedDetail);
      if ((detail.unread_message_count ?? 0) > 0) {
        sessionApi.clearUnreadMessageCount(sessionId).catch(() => undefined);
      }
      setFiles(normalizeFileList(fileListRaw));
    } catch (e) {
      if (isSessionMissingError(e)) {
        onSessionMissing(e);
      } else {
        setError(e instanceof Error ? e : new Error(tCommon("loadFailed")));
      }
    } finally {
      setLoading(false);
    }
  }, [sessionId, normalizeFileList, onSessionMissing, tCommon]);

  const refreshFiles = useCallback(async () => {
    if (!sessionId) return;
    try {
      const fileListRaw = await sessionApi.getSessionFiles(sessionId);
      setFiles(normalizeFileList(fileListRaw));
    } catch {
      setError(new Error(t("refreshFilesFailed")));
    }
  }, [sessionId, normalizeFileList, t]);

  const updateSessionConfig = useCallback(
    async (params: UpdateSessionConfigParams) => {
      if (!sessionId) return;
      const updated = await sessionApi.updateSessionConfig(sessionId, params);
      setSession(updated);
    },
    [sessionId],
  );

  const applySessionPatch = useCallback((patch: Partial<SessionDetail>) => {
    setSession((prev) => (prev ? { ...prev, ...patch } : null));
  }, []);

  const resetMeta = useCallback(() => {
    setSession(null);
    setFiles([]);
    setCheckpoints([]);
    setError(null);
    setLoading(false);
  }, []);

  return {
    session,
    files,
    checkpoints,
    loading,
    error,
    setError,
    refreshMeta,
    refreshFiles,
    updateSessionConfig,
    applySessionPatch,
    resetMeta,
  };
}
