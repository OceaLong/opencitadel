"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

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

import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeBase } from "@/lib/api/types";

type CreateKBDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (kb: KnowledgeBase) => void;
};

export function CreateKBDialog({ open, onOpenChange, onCreated }: CreateKBDialogProps) {
  const t = useTranslations("knowledge.createDialog");
  const tCommon = useTranslations("common");
  const tKnowledge = useTranslations("knowledge");
  const [name, setName] = useState("");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [creating, setCreating] = useState(false);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      const kb = await knowledgeApi.create({
        name: name || t("defaultName"),
        ...(verifiedOnly ? { settings: { embedding_revision_policy: "verified_only" } } : {}),
      });
      onCreated(kb);
      onOpenChange(false);
      setName("");
      setVerifiedOnly(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("createFailed"));
    } finally {
      setCreating(false);
    }
  }, [name, verifiedOnly, onCreated, onOpenChange, t]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <p className="text-muted-foreground text-sm">{t("policyHint")}</p>
          <Label htmlFor="kb-name">{tCommon("name")}</Label>
          <Input
            id="kb-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("namePlaceholder")}
          />
          <Label htmlFor="kb-verified-embedding" className="flex items-start gap-2 text-sm">
            <input
              id="kb-verified-embedding"
              type="checkbox"
              checked={verifiedOnly}
              onChange={(event) => setVerifiedOnly(event.target.checked)}
            />
            {tKnowledge("embeddingSpaceStrict")}
          </Label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={handleCreate} disabled={creating}>
            {creating && <Loader2 className="mr-2 size-4 animate-spin" />}
            {tCommon("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
