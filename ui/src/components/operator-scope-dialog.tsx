"use client";

import { useState } from "react";
import { AlertTriangle, Check } from "lucide-react";
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

import { cn } from "@/lib/utils";

export type OperatorScope = "owned" | "third_party_saas";

type OperatorScopeDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (scope: OperatorScope) => void;
};

export function OperatorScopeDialog({
  open,
  onOpenChange,
  onConfirm,
}: OperatorScopeDialogProps) {
  const [scope, setScope] = useState<OperatorScope>("owned");
  const t = useTranslations("operatorScope");
  const tCommon = useTranslations("common");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <button
            type="button"
            className={cn(
              "hover:bg-muted/60 w-full rounded-lg border p-3 text-left transition-colors",
              scope === "owned" && "border-primary bg-primary/5",
            )}
            onClick={() => setScope("owned")}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium">{t("ownedTitle")}</span>
              {scope === "owned" && <Check className="text-primary size-4" />}
            </div>
            <p className="text-muted-foreground mt-1 text-xs">{t("ownedDescription")}</p>
          </button>
          <button
            type="button"
            className={cn(
              "hover:bg-muted/60 w-full rounded-lg border border-amber-500/40 p-3 text-left transition-colors",
              scope === "third_party_saas" && "border-amber-600 bg-amber-500/10",
            )}
            onClick={() => setScope("third_party_saas")}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium">{t("thirdPartyTitle")}</span>
              {scope === "third_party_saas" && <Check className="size-4 text-amber-700" />}
            </div>
            <p className="text-muted-foreground mt-1 flex items-start gap-1 text-xs">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
              {t("thirdPartyDescription")}
            </p>
          </button>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={() => {
              onConfirm(scope);
              onOpenChange(false);
            }}
          >
            {t("confirmCreate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
