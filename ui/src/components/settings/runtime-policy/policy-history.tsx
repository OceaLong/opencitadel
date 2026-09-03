"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  type ActiveExecutionPolicy,
  type ActiveOperationsPolicy,
  type ExecutionPolicyRevision,
  type OperationsPolicyRevision,
  runtimePolicyApi,
  type RuntimePolicyHead,
} from "@/lib/api/runtime-policies";
import { toBcp47 } from "@/lib/utils";

type Props = {
  kind: "execution" | "operations";
  head: RuntimePolicyHead;
  revisions: Array<ExecutionPolicyRevision | OperationsPolicyRevision>;
  onRestored: (active: ActiveExecutionPolicy | ActiveOperationsPolicy) => void | Promise<void>;
};

export function PolicyHistory({ kind, head, revisions, onRestored }: Props) {
  const t = useTranslations("runtimePolicy");
  const locale = useLocale();
  const [selected, setSelected] = useState<ExecutionPolicyRevision | OperationsPolicyRevision>();
  const [restoring, setRestoring] = useState(false);

  const restore = async () => {
    if (!selected) return;
    setRestoring(true);
    try {
      const body = {
        expected_head_version: head.version,
        expected_active_revision_id:
          kind === "execution" ? head.execution_revision_id : head.operations_revision_id,
        note: t("history.restoreNote", { sequence: selected.sequence }),
      };
      const active =
        kind === "execution"
          ? await runtimePolicyApi.restoreExecution(selected.id, body)
          : await runtimePolicyApi.restoreOperations(selected.id, body);
      await onRestored(active);
      setSelected(undefined);
      toast.success(t("history.restoreSuccess"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("history.restoreFailed"));
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div className="space-y-3">
      <h4 className="font-medium">{t("history.title")}</h4>
      {revisions.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("history.empty")}</p>
      ) : (
        <div className="space-y-2">
          {revisions.map((revision) => (
            <div
              key={revision.id}
              className="flex items-center justify-between gap-3 rounded-md border p-3"
            >
              <div className="min-w-0 text-sm">
                <p className="font-medium">
                  {t("history.revision", { sequence: revision.sequence })} · {revision.note}
                </p>
                <p className="text-muted-foreground truncate text-xs">
                  {revision.created_by} ·{" "}
                  {new Date(revision.created_at).toLocaleString(toBcp47(locale))}
                </p>
              </div>
              <Button
                type="button"
                size="xs"
                variant="outline"
                disabled={
                  restoring ||
                  revision.id ===
                    (kind === "execution"
                      ? head.execution_revision_id
                      : head.operations_revision_id)
                }
                onClick={() => setSelected(revision)}
              >
                {t("history.restoreRevision", { sequence: revision.sequence })}
              </Button>
            </div>
          ))}
        </div>
      )}

      <Dialog open={Boolean(selected)} onOpenChange={(open) => !open && setSelected(undefined)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("history.confirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("history.confirmDescription", { sequence: selected?.sequence ?? "" })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={restoring}>
                {t("actions.cancel")}
              </Button>
            </DialogClose>
            <Button type="button" disabled={restoring} onClick={restore}>
              {restoring ? <Loader2 className="animate-spin" /> : null}
              {t("history.confirmRestore")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
