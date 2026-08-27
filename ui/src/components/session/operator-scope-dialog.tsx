"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { cn } from "@/lib/utils";

export type OperatorScope = "owned" | "third_party_saas";

export type OperatorSessionConfig = {
  scope: OperatorScope;
  operatorDomains: string[];
};

type OperatorScopeDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (config: OperatorSessionConfig) => void;
  mode?: "create" | "edit";
  initialConfig?: Partial<OperatorSessionConfig>;
};

const DEFAULT_CONFIG: OperatorSessionConfig = {
  scope: "owned",
  operatorDomains: ["ops-console", "localhost"],
};

export function OperatorScopeDialog({
  open,
  onOpenChange,
  onConfirm,
  mode = "create",
  initialConfig,
}: OperatorScopeDialogProps) {
  const [scope, setScope] = useState<OperatorScope>(DEFAULT_CONFIG.scope);
  const [domainsText, setDomainsText] = useState(DEFAULT_CONFIG.operatorDomains.join(", "));
  const t = useTranslations("operatorScope");
  const tCommon = useTranslations("common");
  const isEdit = mode === "edit";

  useEffect(() => {
    if (!open) return;
    const nextScope = initialConfig?.scope ?? DEFAULT_CONFIG.scope;
    const nextDomains = initialConfig?.operatorDomains ?? DEFAULT_CONFIG.operatorDomains;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setScope(nextScope);
      setDomainsText(nextDomains.join(", "));
    });
    return () => {
      cancelled = true;
    };
  }, [open, initialConfig]);

  const parseDomains = (raw: string): string[] =>
    raw
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  const operatorDomains = parseDomains(domainsText);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("editTitle") : t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {!isEdit && (
            <div className="space-y-2">
              <Label>{t("ownershipLabel")}</Label>
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
                  "hover:bg-muted/60 border-approval-subtle w-full rounded-lg border p-3 text-left transition-colors",
                  scope === "third_party_saas" && "border-approval bg-approval-subtle",
                )}
                onClick={() => setScope("third_party_saas")}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{t("thirdPartyTitle")}</span>
                  {scope === "third_party_saas" && <Check className="text-warning size-4" />}
                </div>
                <p className="text-muted-foreground mt-1 flex items-start gap-1 text-xs">
                  <AlertTriangle className="text-warning mt-0.5 size-3.5 shrink-0" />
                  {t("thirdPartyDescription")}
                </p>
              </button>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="operator-domains">{t("domainsLabel")}</Label>
            <Input
              id="operator-domains"
              value={domainsText}
              onChange={(e) => setDomainsText(e.target.value)}
              placeholder={t("domainsPlaceholder")}
            />
            <p className="text-muted-foreground text-xs">{t("domainsHint")}</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            disabled={operatorDomains.length === 0}
            onClick={() => {
              onConfirm({
                scope,
                operatorDomains,
              });
              onOpenChange(false);
            }}
          >
            {isEdit ? t("save") : t("confirmCreate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
