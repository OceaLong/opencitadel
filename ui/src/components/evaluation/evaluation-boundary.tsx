"use client";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { useCapabilities } from "@/hooks/use-capabilities";
import { type ExecutionGrant, hasExecutionGrant } from "@/lib/api/capabilities";
import { ApiError, type RequestOptions } from "@/lib/api/fetch";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

export type EvaluationAccess = {
  workspaceId: string;
  canManage: boolean;
  canRun: boolean;
  canRegister: boolean;
  canReview?: boolean;
  deny?: () => void;
};
export function EvaluationBoundary({
  children,
}: {
  children: (access: EvaluationAccess) => ReactNode;
}) {
  const t = useTranslations("evaluations");
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const capabilities = useCapabilities();
  const [deniedKey, setDeniedKey] = useState<string | null>(null);
  const key = JSON.stringify([scope, scopeRevision]);
  const deny = useCallback(() => setDeniedKey(key), [key]);
  if (loading || (capabilities.loading && !capabilities.snapshot))
    return (
      <p role="status" className="p-3">
        {t("loading")}
      </p>
    );
  if (
    deniedKey === key ||
    !user ||
    !scope ||
    scope.userId !== user.id ||
    !hasExecutionGrant(capabilities.snapshot ?? undefined, "evaluation.read")
  )
    return (
      <p role="alert" className="p-3">
        {t("denied")}
      </p>
    );
  const grant = (value: ExecutionGrant) =>
    hasExecutionGrant(capabilities.snapshot ?? undefined, value);
  return (
    <main key={JSON.stringify([scope, scopeRevision])} className="h-full min-w-0 overflow-y-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 p-3 pb-24 sm:gap-6 sm:p-6">
        {children({
          workspaceId: scope.workspaceId,
          canManage: grant("evaluation.manage"),
          canRun: grant("evaluation.run"),
          canRegister: grant("evaluation.environment.manage"),
          canReview: grant("evaluation.review"),
          deny,
        })}
      </div>
    </main>
  );
}

/** Serial form commands and reads carry one immutable scope; all callbacks are fenced. */
export function useEvaluationTask(access: EvaluationAccess) {
  const { workspaceId, deny } = access;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<"conflict" | "denied" | "unavailable" | "failed" | null>(null);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const busy = useRef(false);
  const ids = useRef(new Map<string, string>());
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      busy.current = false;
      controller.current?.abort();
    };
  }, []);
  const cancel = useCallback(() => {
    controller.current?.abort();
    ++generation.current;
    busy.current = false;
    setPending(false);
    setError(null);
  }, []);
  const requestId = useCallback((operation: string, body: unknown) => {
    const key = JSON.stringify([operation, body]);
    let id = ids.current.get(key);
    if (!id) {
      id = crypto.randomUUID();
      ids.current.set(key, id);
    }
    return id;
  }, []);
  const run = useCallback(
    async <T,>(
      job: (options: RequestOptions) => Promise<T>,
      apply: (value: T) => void,
      replace = false,
    ) => {
      if (busy.current && !replace) return;
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      const serial = ++generation.current;
      const active = () =>
        mounted.current && serial === generation.current && !abort.signal.aborted;
      busy.current = true;
      setPending(true);
      setError(null);
      try {
        const value = await job({ workspaceId, signal: abort.signal });
        if (active()) apply(value);
      } catch (failure) {
        if (active()) {
          if (failure instanceof ApiError && [401, 403].includes(failure.code)) deny?.();
          setError(
            failure instanceof ApiError
              ? failure.code === 401 || failure.code === 403
                ? "denied"
                : failure.code === 409
                  ? "conflict"
                  : failure.code === 404
                    ? "unavailable"
                    : "failed"
              : "failed",
          );
        }
      } finally {
        if (active()) {
          busy.current = false;
          setPending(false);
        }
      }
    },
    [workspaceId, deny],
  );
  return { run, pending, error, requestId, cancel };
}
export function EvaluationError({
  error,
  refresh,
}: {
  error: string | null;
  refresh?: () => void;
}) {
  const t = useTranslations("evaluations");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) ref.current?.focus();
  }, [error]);
  if (!error) return null;
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      className="border-warning rounded-md border p-3 text-sm"
    >
      <p>{t(error as "conflict")}</p>
      {refresh && (
        <Button variant="outline" onClick={refresh}>
          {t("refresh")}
        </Button>
      )}
    </div>
  );
}
