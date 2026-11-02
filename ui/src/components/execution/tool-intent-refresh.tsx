"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { ApiError } from "@/lib/api/fetch";
import { toolIntentRefreshApi, type ToolIntentRefreshRequest } from "@/lib/api/tool-intent-refresh";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

export function ToolIntentRefreshAction(props: {
  runId: string;
  activityId: string;
  subjectDigest: string;
  onChanged: () => void;
  onRevoked: () => void;
  disabled?: boolean;
}) {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  if (loading || !user || !scope || scope.userId !== user.id || user.global_role === "auditor")
    return null;
  return (
    <RefreshForm
      key={`${user.id}:${scope.workspaceId}:${scopeRevision}:${props.activityId}:${props.subjectDigest}`}
      {...props}
      workspaceId={scope.workspaceId}
    />
  );
}

export function RefreshForm({
  runId,
  activityId,
  subjectDigest,
  workspaceId,
  onChanged,
  onRevoked,
  disabled = false,
}: {
  runId: string;
  activityId: string;
  subjectDigest: string;
  workspaceId: string;
  onChanged: () => void;
  onRevoked: () => void;
  disabled?: boolean;
}) {
  const t = useTranslations("executionEffects");
  const [reason, setReason] = useState("");
  const [request, setRequest] = useState<ToolIntentRefreshRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const key = useRef<string | null>(null);
  const inFlight = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (request?.status !== "pending") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await toolIntentRefreshApi.status(runId, activityId, request.command_id, {
          workspaceId,
          signal: controller.signal,
          skipErrorHandler: true,
        });
        if (controller.signal.aborted) return;
        setRequest(next);
        if (next.status === "applied") onChanged();
        if (next.status === "rejected") key.current = null;
        if (next.status === "pending") timer = setTimeout(poll, 1500);
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && [403, 404].includes(cause.code)) {
          onRevoked();
          return;
        }
        setError(true);
        timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [request?.command_id, request?.status, runId, activityId, workspaceId, onChanged, onRevoked]);
  async function submit() {
    if (
      disabled ||
      inFlight.current ||
      !reason.trim() ||
      request?.status === "pending" ||
      request?.status === "applied"
    )
      return;
    inFlight.current = true;
    setBusy(true);
    setError(false);
    key.current ??= crypto.randomUUID();
    try {
      const next = await toolIntentRefreshApi.submit(
        runId,
        activityId,
        {
          expected_subject_digest: subjectDigest,
          reason: reason.trim(),
        },
        key.current,
        { workspaceId, skipErrorHandler: true },
      );
      if (alive.current) setRequest(next);
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof ApiError && [403, 404].includes(cause.code)) onRevoked();
      setError(true);
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const pending = busy || request?.status === "pending" || request?.status === "applied";
  return (
    <section className="space-y-2 rounded border p-3" aria-label={t("refreshIntent")}>
      <p className="text-sm">{t("refreshIntentHelp")}</p>
      <Textarea
        aria-label={t("refreshReason")}
        placeholder={t("refreshReason")}
        maxLength={2000}
        value={reason}
        disabled={pending || disabled}
        onChange={(event) => {
          setReason(event.target.value);
          key.current = null;
          setRequest(null);
          setError(false);
        }}
      />
      <Button
        size="sm"
        onClick={() => void submit()}
        disabled={disabled || pending || !reason.trim()}
      >
        {t("refreshIntent")}
      </Button>
      {request?.status === "pending" && <p role="status">{t("refreshQueued")}</p>}
      {request?.status === "applied" && <p role="status">{t("refreshApplied")}</p>}
      {request?.status === "rejected" && <p role="alert">{t("refreshRejected")}</p>}
      {error && <p role="alert">{t("refreshError")}</p>}
    </section>
  );
}
