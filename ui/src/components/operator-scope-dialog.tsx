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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { cn } from "@/lib/utils";

export type OperatorScope = "owned" | "third_party_saas";
export type GateProfile = "loose" | "standard" | "strict";

export type OperatorSessionConfig = {
  scope: OperatorScope;
  operatorDomains: string[];
  gateProfile: GateProfile;
};

type OperatorScopeDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (config: OperatorSessionConfig) => void;
};

const GATE_PROFILES: GateProfile[] = ["loose", "standard", "strict"];

export function OperatorScopeDialog({
  open,
  onOpenChange,
  onConfirm,
}: OperatorScopeDialogProps) {
  const [scope, setScope] = useState<OperatorScope>("owned");
  const [domainsText, setDomainsText] = useState("ops-console");
  const [gateProfile, setGateProfile] = useState<GateProfile>("standard");
  const t = useTranslations("operatorScope");
  const tCommon = useTranslations("common");

  const parseDomains = (raw: string): string[] =>
    raw
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
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

          <div className="space-y-2">
            <Label>{t("gateProfileLabel")}</Label>
            <div className="grid gap-2">
              {GATE_PROFILES.map((profile) => (
                <button
                  key={profile}
                  type="button"
                  className={cn(
                    "hover:bg-muted/60 rounded-lg border p-3 text-left transition-colors",
                    gateProfile === profile && "border-primary bg-primary/5",
                  )}
                  onClick={() => setGateProfile(profile)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{t(`gateProfile.${profile}.title`)}</span>
                    {gateProfile === profile && <Check className="text-primary size-4" />}
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {t(`gateProfile.${profile}.description`)}
                  </p>
                </button>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={() => {
              onConfirm({
                scope,
                operatorDomains: parseDomains(domainsText),
                gateProfile,
              });
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
