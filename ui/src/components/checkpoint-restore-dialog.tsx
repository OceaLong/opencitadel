"use client";

import { useTranslations } from "next-intl";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { SessionCheckpoint } from "@/lib/api/types";

type CheckpointRestoreDialogProps = {
  checkpoint: SessionCheckpoint | null;
  open: boolean;
  restoring: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
};

export function CheckpointRestoreDialog({
  checkpoint,
  open,
  restoring,
  onOpenChange,
  onConfirm,
}: CheckpointRestoreDialogProps) {
  const t = useTranslations("checkpoint");
  const tCommon = useTranslations("common");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t("description", { label: checkpoint?.label || tCommon("here") })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={restoring}
          >
            {tCommon("cancel")}
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={restoring}>
            {restoring ? t("restoring") : t("confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
