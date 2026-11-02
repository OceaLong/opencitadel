"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import {
  type EffectEvidenceRef,
  type EffectOperation,
  type EffectRequest,
  type EffectResolution,
  executionEffectsApi,
} from "@/lib/api/execution-effects";
import { ApiError, type RequestOptions } from "@/lib/api/fetch";
import { fileApi } from "@/lib/api/file";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

/** Parent workbench's cursor-resuming event subscription supplies revision invalidation. */
export function ExecutionEffects({
  runId,
  revision,
  historical = false,
}: {
  runId: string;
  revision: number;
  historical?: boolean;
}) {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  if (loading || !user || !scope || scope.userId !== user.id || !runId || historical) return null;
  return (
    <EffectResolutionPanel
      key={`${user.id}:${scope.workspaceId}:${scopeRevision}:${runId}`}
      runId={runId}
      workspaceId={scope.workspaceId}
      actorId={user.id}
      revision={revision}
      readOnly={user.global_role === "auditor"}
    />
  );
}

export function EffectResolutionPanel({
  runId,
  workspaceId,
  actorId,
  revision,
  readOnly = false,
}: {
  runId: string;
  workspaceId: string;
  actorId: string;
  revision: number;
  readOnly?: boolean;
}) {
  const t = useTranslations("executionEffects");
  const [operations, setOperations] = useState<EffectOperation[]>([]);
  const [error, setError] = useState(false);
  const [denied, setDenied] = useState(false);
  const sequence = useRef(0);
  const lifetime = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    const controller = lifetime.current;
    if (!controller || controller.signal.aborted) return;
    const options = { workspaceId, signal: controller.signal, skipErrorHandler: true };
    try {
      const items = await executionEffectsApi.list(runId, options);
      // Read details so request history and allowed actions survive navigation/reload.
      const details = await Promise.all(
        items.map((item) => executionEffectsApi.get(runId, item.operation_id, options)),
      );
      if (controller.signal.aborted || current !== sequence.current) return;
      setOperations(details);
      setError(false);
    } catch (cause) {
      if (controller.signal.aborted || current !== sequence.current) return;
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.code)) {
        setOperations([]);
        setDenied(true);
        controller.abort();
      } else setError(true);
    }
  }, [runId, workspaceId]);
  useEffect(() => {
    lifetime.current = new AbortController();
    const controller = lifetime.current;
    void refresh();
    // Request progress can change without a formal Run event (revocation/conflict).
    const timer = setInterval(() => void refresh(), 5000);
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    return () => {
      controller.abort();
      clearInterval(timer);
      window.removeEventListener("focus", focus);
    };
  }, [refresh]);
  useEffect(() => {
    void refresh();
  }, [revision, refresh]);
  if (denied) return <p role="status">{t("accessRevoked")}</p>;
  if (!operations.length && !error) return null;
  return (
    <section
      aria-label={t("title")}
      className="max-h-[25vh] space-y-3 overflow-y-auto rounded-md border p-3"
    >
      <h2 className="font-medium">{t("title")}</h2>
      {error && (
        <p role="alert">
          {t("loadFailed")}{" "}
          <Button size="sm" onClick={() => void refresh()}>
            {t("refresh")}
          </Button>
        </p>
      )}
      {operations.map((operation) => (
        <EffectCard
          key={`${operation.operation_id}:${operation.revision}`}
          operation={operation}
          runId={runId}
          workspaceId={workspaceId}
          actorId={actorId}
          readOnly={readOnly || error}
          onChanged={refresh}
          onDenied={() => {
            setOperations([]);
            setDenied(true);
            lifetime.current?.abort();
          }}
        />
      ))}
    </section>
  );
}

function EffectCard({
  operation,
  runId,
  workspaceId,
  actorId,
  readOnly,
  onChanged,
  onDenied,
}: {
  operation: EffectOperation;
  runId: string;
  workspaceId: string;
  actorId: string;
  readOnly: boolean;
  onChanged: () => Promise<void>;
  onDenied: () => void;
}) {
  const t = useTranslations("executionEffects");
  const [action, setAction] = useState<"resolve" | "abandon" | null>(null);
  const [decision, setDecision] = useState<"applied" | "not_applied">("applied");
  const [reason, setReason] = useState("");
  const [source, setSource] = useState("");
  const [method, setMethod] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [evidence, setEvidence] = useState<EffectEvidenceRef | null>(null);
  const [result, setResult] = useState<EffectEvidenceRef | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [request, setRequest] = useState<EffectRequest | null>(null);
  // Retain a key for an identical uncertain transport retry, never invent a new business attempt.
  const command = useRef<{ body: string; key: string } | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const c = new AbortController();
    lifetime.current = c;
    return () => c.abort();
  }, []);
  const options = (): RequestOptions => ({
    workspaceId,
    signal: lifetime.current?.signal,
    skipErrorHandler: true,
  });
  const allowed = (name: string) => !readOnly && operation.allowed_actions.includes(name);
  const handleError = (cause: unknown) => {
    if (lifetime.current?.signal.aborted) return;
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.code)) onDenied();
    else if (cause instanceof ApiError && cause.code === 409) {
      setMessage(t("conflict"));
      void onChanged();
    } else if (cause instanceof ApiError && cause.code === 422) setMessage(t("invalidEvidence"));
    else setMessage(t("requestFailed"));
  };
  const submit = async (kind: "query" | "resolve" | "abandon") => {
    if (busy || readOnly) return;
    const body: EffectResolution = {
      expected_revision: operation.revision,
      decision,
      reason: reason.trim(),
      evidence_refs: evidence ? [evidence] : [],
      result_evidence_ref: result,
      attestation: {
        source: source.trim(),
        method: method.trim(),
        occurred_at: occurredAt ? new Date(occurredAt).toISOString() : "",
        responsible_actor: actorId,
        action: operation.verification_binding.action ?? "",
        target: operation.target_digest,
      },
    };
    const identity = JSON.stringify([
      operation.operation_id,
      kind,
      kind === "resolve" ? body : [operation.revision, kind === "abandon" ? reason : null],
    ]);
    if (command.current?.body !== identity)
      command.current = { body: identity, key: crypto.randomUUID() };
    setBusy(true);
    setMessage("");
    try {
      const response =
        kind === "query"
          ? await executionEffectsApi.query(
              runId,
              operation.operation_id,
              operation.revision,
              command.current.key,
              options(),
            )
          : kind === "abandon"
            ? await executionEffectsApi.abandon(
                runId,
                operation.operation_id,
                operation.revision,
                reason.trim(),
                command.current.key,
                options(),
              )
            : await executionEffectsApi.resolve(
                runId,
                operation.operation_id,
                body,
                command.current.key,
                options(),
              );
      if (lifetime.current?.signal.aborted) return;
      setRequest(response);
      setAction(null);
      await onChanged();
    } catch (cause) {
      handleError(cause);
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy(false);
    }
  };
  const upload = async (file: File | undefined, output: boolean) => {
    if (!file || busy) return;
    setBusy(true);
    setMessage("");
    try {
      if (file.size > 1024 * 1024) throw new Error("evidence too large");
      const text = await file.text();
      if (!text.trim()) throw new Error("empty evidence");
      let content: unknown = text;
      try {
        content = JSON.parse(text);
      } catch {
        if (output) throw new Error("result must be JSON");
      }
      if (output && (!content || typeof content !== "object" || Array.isArray(content)))
        throw new Error("result must be an object");
      const payload = {
        schema_version: 1,
        operation_id: operation.operation_id,
        contract_digest: operation.contract_digest,
        arguments_digest: operation.arguments_digest,
        target_digest: operation.target_digest,
        ...(output
          ? { result: content }
          : { evidence: { filename: file.name, media_type: file.type, content } }),
      };
      const packageFile = new File(
        [JSON.stringify(payload)],
        `effect-${output ? "result" : "evidence"}.json`,
        { type: "application/json" },
      );
      if (packageFile.size > 1024 * 1024) throw new Error("evidence package too large");
      const stored = await fileApi.uploadFile({ file: packageFile }, options());
      if (lifetime.current?.signal.aborted) return;
      if (!stored.content_digest) throw new Error("missing content identity");
      const reference = { ref: `file://${stored.id}`, sha256: stored.content_digest };
      if (output) setResult(reference);
      else setEvidence(reference);
    } catch (cause) {
      handleError(cause);
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy(false);
    }
  };
  const history = [...operation.query_history];
  if (request && !history.some((item) => item.request_id === request.request_id))
    history.push(request);
  const stateLabels: Record<string, string> = {
    prepared: t("states.prepared"),
    sending: t("states.sending"),
    pending: t("states.pending"),
    unknown: t("states.unknown"),
    applied: t("states.applied"),
    not_applied: t("states.not_applied"),
  };
  const requestLabels: Record<string, string> = {
    pending: t("requestStates.pending"),
    running: t("requestStates.running"),
    completed: t("requestStates.completed"),
    failed: t("requestStates.failed"),
    paused: t("requestStates.paused"),
    revoked: t("requestStates.revoked"),
  };
  const resolutionValid = Boolean(
    evidence &&
    reason.trim() &&
    source.trim() &&
    method.trim() &&
    occurredAt &&
    operation.verification_binding.action &&
    (operation.wait_reason !== "result_material_missing" || result),
  );
  return (
    <article className="space-y-2 border-t pt-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt>{t("operation")}</dt>
        <dd className="break-all" translate="no">
          {operation.operation_id}
        </dd>
        <dt>{t("businessState")}</dt>
        <dd>{stateLabels[operation.state] ?? operation.state}</dd>
        <dt>{t("waitReason")}</dt>
        <dd>
          {operation.wait_reason === "external_evidence_conflict"
            ? t("evidenceConflict")
            : (operation.wait_reason ?? "—")}
        </dd>
        <dt>{t("accounting")}</dt>
        <dd>{t("independentAccounting")}</dd>
      </dl>
      {operation.last_confirmed_fact && (
        <details>
          <summary>{t("lastFact")}</summary>
          <pre className="overflow-auto text-xs">
            {JSON.stringify(operation.last_confirmed_fact, null, 2)}
          </pre>
        </details>
      )}
      <details>
        <summary>{t("capabilities")}</summary>
        <pre className="overflow-auto text-xs">{JSON.stringify(operation.capability, null, 2)}</pre>
      </details>
      {operation.contract_drift && (
        <div role="alert">
          <p>{t("contractDrift")}</p>
          <pre className="overflow-auto text-xs">
            {JSON.stringify(operation.contract_drift, null, 2)}
          </pre>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {allowed("query") && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void submit("query")}>
            {t("query")}
          </Button>
        )}
        {(allowed("resolve") || allowed("supply_result")) && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => {
              setAction("resolve");
              setDecision("applied");
            }}
          >
            {operation.wait_reason === "result_material_missing" ? t("supplyResult") : t("resolve")}
          </Button>
        )}
        {allowed("abandon") && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setAction("abandon")}>
            {t("abandon")}
          </Button>
        )}
      </div>
      {action && !readOnly && (
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(action);
          }}
        >
          {action === "resolve" ? (
            <>
              <p className="text-xs">{t("humanEvidence")}</p>
              <label>
                {t("decision")}
                <select
                  aria-label={t("decision")}
                  value={decision}
                  disabled={busy || operation.state === "applied"}
                  onChange={(event) => setDecision(event.target.value as "applied" | "not_applied")}
                  className="ml-2 rounded border p-1"
                >
                  <option value="applied">{t("states.applied")}</option>
                  <option value="not_applied">{t("states.not_applied")}</option>
                </select>
              </label>
              <label>
                {t("evidenceFile")}
                <input
                  type="file"
                  accept="application/json,text/plain,.json,.txt,.log"
                  disabled={busy}
                  onChange={(event) => void upload(event.target.files?.[0], false)}
                />
              </label>
              {evidence && <p role="status">{t("evidenceAttached")}</p>}
              <label>
                {t("resultFile")}
                <input
                  type="file"
                  accept="application/json,.json"
                  disabled={busy}
                  onChange={(event) => void upload(event.target.files?.[0], true)}
                />
              </label>
              {result && <p role="status">{t("resultAttached")}</p>}
              <label>
                {t("source")}
                <input
                  required
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  className="block w-full rounded border p-2"
                />
              </label>
              <label>
                {t("occurredAt")}
                <input
                  required
                  type="datetime-local"
                  value={occurredAt}
                  onChange={(event) => setOccurredAt(event.target.value)}
                  className="block rounded border p-2"
                />
              </label>
              <label>
                {t("method")}
                <textarea
                  required
                  value={method}
                  onChange={(event) => setMethod(event.target.value)}
                  className="block w-full rounded border p-2"
                />
              </label>
              {decision === "not_applied" && <p>{t("noAutomaticRetry")}</p>}
            </>
          ) : (
            <p>{t("abandonWarning")}</p>
          )}
          <label>
            {t("reason")}
            <textarea
              required
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="block w-full rounded border p-2"
            />
          </label>
          <div className="flex gap-2">
            <Button
              type="submit"
              size="sm"
              disabled={busy || (action === "resolve" ? !resolutionValid : !reason.trim())}
            >
              {t("submit")}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setAction(null)}>
              {t("cancel")}
            </Button>
          </div>
        </form>
      )}
      {message && <p role="alert">{message}</p>}
      {history.length > 0 && (
        <details open>
          <summary>{t("requests")}</summary>
          <ul className="space-y-1 text-xs">
            {history.map((item) => (
              <li key={item.request_id}>
                <span translate="no">{item.request_id}</span> ·{" "}
                {requestLabels[item.status] ?? item.status} · {item.updated_at}
                {item.error_code && <span role="status"> · {item.error_code}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
