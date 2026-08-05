"use client";

import { useState } from "react";
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

type DeleteSessionDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
};

/**
 * 删除任务确认弹窗
 * 确认后才发起 API 删除请求
 */
export function DeleteSessionDialog({ open, onOpenChange, onConfirm }: DeleteSessionDialogProps) {
  const t = useTranslations("deleteSession");
  const tCommon = useTranslations("common");
  const [deleting, setDeleting] = useState(false);

  const handleConfirm = async () => {
    setDeleting(true);
    try {
      await onConfirm();
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="text-lg font-semibold">{t("title")}</DialogTitle>
          <DialogDescription className="text-muted-foreground text-sm leading-relaxed">
            {t("description")}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            className="cursor-pointer"
            onClick={() => onOpenChange(false)}
            disabled={deleting}
          >
            {tCommon("cancel")}
          </Button>
          <Button className="cursor-pointer" onClick={handleConfirm} disabled={deleting}>
            {deleting ? t("deleting") : tCommon("confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
