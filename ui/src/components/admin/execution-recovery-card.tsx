"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { executionRecoveryApi, type ExecutionRecoveryStatus } from "@/lib/api/execution-recovery";

export function ExecutionRecoveryCard() {
  const t = useTranslations("executionRecovery");
  const [status, setStatus] = useState<ExecutionRecoveryStatus | null>(null);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      setStatus(await executionRecoveryApi.status());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const scopes = [
    ...new Set([
      ...(status?.poisoned_scopes.map((item) => item.owner_scope_key) ?? []),
      ...(status?.poisoned_runs.map((item) => item.owner_scope_key) ?? []),
      ...(status?.scope_lags.map((item) => item.owner_scope_key) ?? []),
    ]),
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button variant="outline" onClick={() => void load()}>
          {t("refresh")}
        </Button>
        {error && (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        )}
        {!status && !error && <p>{t("loading")}</p>}
        {status && scopes.length === 0 && <p>{t("healthy")}</p>}
        {status?.scope_lags.map((item) => (
          <p key={item.owner_scope_key} className="text-sm">
            {item.owner_scope_key}: {t("lag", { count: item.lag })}
          </p>
        ))}
        {status?.poisoned_scopes.map((item) => (
          <p key={item.owner_scope_key} className="text-sm">
            {item.owner_scope_key}: {item.reason} — {item.last_error}
          </p>
        ))}
        {status?.poisoned_runs.map((item) => (
          <div key={item.run_id} className="rounded border p-3 text-sm">
            <p>
              {item.run_id} · {item.owner_scope_key}
            </p>
            <p>
              {item.reason}: {item.last_error}
            </p>
            <p>
              {item.next_attempt_at
                ? t("retryAt", { time: item.next_attempt_at })
                : t("quarantined")}
            </p>
          </div>
        ))}
        <form
          className="space-y-2"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              await executionRecoveryApi.request(scope, reason);
              setReason("");
              await load();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Label htmlFor="recovery-scope">{t("scope")}</Label>
          <Input
            id="recovery-scope"
            translate="no"
            list="recovery-scopes"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
            placeholder="user:… / team:…"
          />
          <datalist id="recovery-scopes">
            {scopes.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
          <Label htmlFor="recovery-reason">{t("reason")}</Label>
          <Input
            id="recovery-reason"
            maxLength={500}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button
            type="submit"
            disabled={busy || !/^(user|team):\S+$/.test(scope) || !reason.trim()}
          >
            {t("recover")}
          </Button>
        </form>
        {status?.recovery_requests.map((item) => (
          <div key={item.id} className="rounded border p-3 text-sm">
            <p>
              {item.owner_scope_key} · {item.status}
            </p>
            <p>{item.reason}</p>
            <pre className="overflow-auto text-xs">{JSON.stringify(item.result, null, 2)}</pre>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
