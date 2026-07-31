"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { sessionApi } from "@/lib/api/session";
import type { ResourceKind, SessionResourceBinding } from "@/lib/api/types";

type VersionOption = { resource_kind: ResourceKind; version_id: string };
type HistoricalMessage = {
  id: string;
  resource_bindings?: Array<
    Pick<SessionResourceBinding, "binding_id" | "resource_kind" | "resource_id" | "version_id">
  >;
};

type Props = {
  sessionId: string;
  bindings?: SessionResourceBinding[];
  versions?: VersionOption[];
  historicalMessages?: HistoricalMessage[];
  onBindingsChanged?: (bindings: SessionResourceBinding[]) => void;
  onUpgraded?: () => void;
};

/** Shows pins and requires a deliberate confirmation before future turns move. */
export function SessionResourceVersion({
  sessionId,
  bindings: suppliedBindings = [],
  versions = [],
  historicalMessages = [],
  onBindingsChanged,
  onUpgraded,
}: Props) {
  const t = useTranslations("workspaceContext");
  const [pending, setPending] = useState<SessionResourceBinding | null>(null);
  const [upgrading, setUpgrading] = useState(false);
  const [status, setStatus] = useState("");
  const [bindings, setBindings] = useState(suppliedBindings);
  const [available, setAvailable] = useState(versions);
  const refreshBindings = useCallback(async () => {
    const current = await sessionApi.getResourceBindings(sessionId);
    setBindings(current);
    onBindingsChanged?.(current);
    const catalogs = await Promise.all(
      current.map((binding) =>
        sessionApi.getAvailableResourceVersions(sessionId, binding.resource_kind),
      ),
    );
    setAvailable(
      catalogs.flat().map((version) => ({
        resource_kind: version.resource_kind,
        version_id: version.version_id,
      })),
    );
  }, [onBindingsChanged, sessionId]);
  useEffect(() => {
    void refreshBindings().catch(() => undefined);
  }, [refreshBindings]);
  const target = useMemo(
    () =>
      pending &&
      available.find(
        (version) =>
          version.resource_kind === pending.resource_kind &&
          version.version_id !== pending.version_id,
      ),
    [pending, available],
  );

  async function upgrade() {
    if (!pending || !target) return;
    setUpgrading(true);
    try {
      const result = await sessionApi.upgradeResourceBinding(
        sessionId,
        pending.resource_kind,
        target.version_id,
      );
      setStatus(
        t("upgradeSuccess", {
          version: result.current_version_id,
        }),
      );
      await refreshBindings().catch(() => undefined);
      setPending(null);
      onUpgraded?.();
    } catch {
      setStatus(t("upgradeFailed"));
    } finally {
      setUpgrading(false);
    }
  }

  return (
    <section aria-label={t("resourceVersionsAria")} className="border-b p-3">
      <p className="text-xs font-medium">{t("currentResourceVersion")}</p>
      <div className="mt-2 space-y-2">
        {bindings.map((binding) => (
          <div key={binding.binding_id} className="flex items-center justify-between gap-2 text-xs">
            <span>
              {binding.resource_kind}: {binding.version_id}
            </span>
            {available.some(
              (version) =>
                version.resource_kind === binding.resource_kind &&
                version.version_id !== binding.version_id,
            ) && (
              <Button size="xs" variant="outline" onClick={() => setPending(binding)}>
                {t("upgradeContext")}
              </Button>
            )}
          </div>
        ))}
      </div>
      <p aria-live="polite" className="sr-only">
        {status}
      </p>
      {historicalMessages.map((message) => (
        <div key={message.id} data-testid={`message-${message.id}`} className="sr-only">
          {message.resource_bindings?.map((binding) => binding.version_id).join(", ")}
        </div>
      ))}
      <Dialog open={Boolean(pending)} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("upgradeContext")}</DialogTitle>
            <DialogDescription>{t("upgradeContextDescription")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)}>
              {t("cancelUpgrade")}
            </Button>
            <Button disabled={!target || upgrading} onClick={upgrade}>
              {t("confirmUpgrade", { version: target?.version_id ?? "" })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
