"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import {
  type DefinitionInfo,
  type DefinitionPage,
  executionDefinitionsApi as api,
  type WorkerPage,
} from "@/lib/api/execution-definitions";
import { ApiError } from "@/lib/api/fetch";

type Change = { id: string; kind: "head" | "retire"; definition: DefinitionInfo; reason: string };

export function DefinitionManagement({ workspaceId }: { workspaceId: string }) {
  const t = useTranslations("executionDefinitions");
  const [definitions, setDefinitions] = useState<DefinitionPage | null>(null);
  const [workers, setWorkers] = useState<WorkerPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [workerOffset, setWorkerOffset] = useState(0);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [blockedRuns, setBlockedRuns] = useState<string[]>([]);
  const [change, setChange] = useState<Change | null>(null);
  const controller = useRef<AbortController | null>(null);
  const changing = useRef(false);
  const refreshId = useRef(0);
  const refresh = useCallback(
    (signal: AbortSignal) => {
      const identity = ++refreshId.current;
      return Promise.all([
        api.definitions(offset, { workspaceId, signal, skipErrorHandler: true }),
        api.workers(workerOffset, { workspaceId, signal, skipErrorHandler: true }),
      ])
        .then(([d, w]) => {
          if (!signal.aborted && identity === refreshId.current) {
            setDefinitions(d);
            setWorkers(w);
          }
        })
        .catch((e: unknown) => {
          if (!signal.aborted && identity === refreshId.current) {
            setDefinitions(null);
            setWorkers(null);
            setError(e instanceof Error ? e.message : t("failed"));
          }
        });
    },
    [offset, workerOffset, workspaceId, t],
  );
  useEffect(() => {
    const value = new AbortController();
    controller.current = value;
    void refresh(value.signal);
    return () => value.abort();
  }, [refresh]);
  useEffect(() => {
    if (!change) return;
    const value = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let submitted = false;
    const options = { workspaceId, signal: value.signal, skipErrorHandler: true };
    const submit = () =>
      change.kind === "head"
        ? api.head(
            change.definition.family,
            {
              request_id: change.id,
              expected_digest: change.definition.head_digest!,
              target_version: change.definition.definition_version,
              reason: change.reason,
            },
            options,
          )
        : api.retire(
            change.definition.family,
            change.definition.definition_version,
            {
              request_id: change.id,
              expected_manifest_digest: change.definition.manifest_digest,
              reason: change.reason,
            },
            options,
          );
    const advance = async () => {
      try {
        // A transport retry always reuses the exact original request identity/body.
        const result = submitted ? await api.result(change.id, options) : await submit();
        submitted = true;
        if (value.signal.aborted) return;
        if (result.status === "completed") {
          setNotice(t("completed"));
          setError("");
          setChange(null);
          setReason("");
          changing.current = false;
          if (controller.current) await refresh(controller.current.signal);
          return;
        }
        setNotice(t("pending"));
      } catch (e) {
        if (value.signal.aborted) return;
        setError(e instanceof Error ? e.message : t("failed"));
        if (
          e instanceof ApiError &&
          e.data &&
          typeof e.data === "object" &&
          "references" in e.data
        ) {
          const refs = e.data.references as { run_ids?: unknown };
          if (Array.isArray(refs.run_ids)) {
            setBlockedRuns(refs.run_ids.filter((id): id is string => typeof id === "string"));
            setError(t("blocked"));
          }
        }
        if (e instanceof ApiError && [400, 401, 403, 409, 422].includes(e.code)) {
          setChange(null);
          setNotice("");
          changing.current = false;
          if ([401, 403].includes(e.code)) {
            setDefinitions(null);
            setWorkers(null);
          }
          return;
        }
        if (e instanceof ApiError && e.code === 404) submitted = false;
      }
      timer = setTimeout(() => void advance(), 1500);
    };
    void advance();
    return () => {
      value.abort();
      clearTimeout(timer);
    };
  }, [change, workspaceId, refresh, t]);
  const begin = (kind: Change["kind"], definition: DefinitionInfo) => {
    if (changing.current) return;
    changing.current = true;
    setError("");
    setBlockedRuns([]);
    setNotice(t("pending"));
    setChange({ id: crypto.randomUUID(), kind, definition, reason: reason.trim() });
  };
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">{t("title")}</h1>
      <p>{t("description")}</p>
      <Button
        variant="outline"
        disabled={!!change}
        onClick={() => {
          if (controller.current) void refresh(controller.current.signal);
        }}
      >
        {t("refresh")}
      </Button>
      {error && <p role="alert">{error}</p>}
      {blockedRuns.map((id) => (
        <Link key={id} className="block underline" href={`/runs/${encodeURIComponent(id)}`}>
          {t("blockingRun")}
        </Link>
      ))}
      {notice && <p role="status">{notice}</p>}
      <div className="space-y-2">
        <Label htmlFor="definition-reason">{t("reason")}</Label>
        <Input
          id="definition-reason"
          value={reason}
          maxLength={2000}
          disabled={!!change}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      <h2 className="font-semibold">{t("definitions")}</h2>
      {!definitions && !error && <p>{t("loading")}</p>}
      {definitions?.items.length === 0 && <p>{t("empty")}</p>}
      {definitions?.items.map((d) => (
        <div
          key={`${d.family}:${d.definition_version}`}
          className="flex flex-wrap items-center gap-3 rounded border p-3"
        >
          <span>{t("version", { family: d.family, version: d.definition_version })}</span>
          <span>{d.retired_at ? t("retired") : d.is_head ? t("current") : t("retained")}</span>
          <Button
            disabled={!!change || !reason.trim() || d.is_head || !!d.retired_at || !d.head_digest}
            onClick={() => begin("head", d)}
          >
            {t("publish")}
          </Button>
          <Button
            variant="outline"
            disabled={!!change || !reason.trim() || d.is_head || !!d.retired_at}
            onClick={() => begin("retire", d)}
          >
            {t("retire")}
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button
          disabled={!!change || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 25))}
        >
          {t("previous")}
        </Button>
        <Button
          disabled={!!change || definitions?.next_offset == null}
          onClick={() => setOffset(definitions!.next_offset!)}
        >
          {t("next")}
        </Button>
      </div>
      <h2 className="font-semibold">{t("workers")}</h2>
      {workers?.items.length === 0 && <p>{t("noWorkers")}</p>}
      {workers?.items.map((w) => (
        <div key={`${w.worker_id}:${w.boot_id}`} className="rounded border p-3">
          <p>
            {w.worker_id} · {w.available ? t("available") : t("expired")}
          </p>
          {w.definitions.map((d) => (
            <p key={d.manifest_digest}>
              {t("version", { family: d.family, version: d.definition_version })}
            </p>
          ))}
        </div>
      ))}
      <div className="flex gap-2">
        <Button
          disabled={!!change || workerOffset === 0}
          onClick={() => setWorkerOffset(Math.max(0, workerOffset - 25))}
        >
          {t("previous")}
        </Button>
        <Button
          disabled={!!change || workers?.next_offset == null}
          onClick={() => setWorkerOffset(workers!.next_offset!)}
        >
          {t("next")}
        </Button>
      </div>
    </div>
  );
}
