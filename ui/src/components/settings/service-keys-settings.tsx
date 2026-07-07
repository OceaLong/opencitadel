"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
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

import { serviceKeysApi, type CreatedServiceApiKey, type ServiceApiKey } from "@/lib/api/service-keys";
import { formatDateTime } from "@/lib/admin-utils";

export function ServiceKeysSettings() {
  const t = useTranslations("settingsServiceKeys");
  const tCommon = useTranslations("common");
  const [keys, setKeys] = useState<ServiceApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createdKey, setCreatedKey] = useState<CreatedServiceApiKey | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await serviceKeysApi.list();
      setKeys(data.keys.filter((k) => !k.revoked_at));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [tCommon]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) {
      toast.error(t("nameRequired"));
      return;
    }
    setCreating(true);
    try {
      const created = await serviceKeysApi.create(name);
      setCreatedKey(created);
      setCreateOpen(false);
      setNewName("");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("saveFailed"));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (keyId: string) => {
    try {
      await serviceKeysApi.revoke(keyId);
      toast.success(t("revoked"));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("deleteFailed"));
    }
  };

  const copyPlaintext = async (plaintext: string) => {
    try {
      await navigator.clipboard.writeText(plaintext);
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  return (
    <div className="w-full px-1">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-foreground text-lg font-semibold">{t("title")}</h3>
          <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
        </div>
        <Button type="button" size="xs" onClick={() => setCreateOpen(true)}>
          <Plus className="mr-1 size-4" />
          {t("createKey")}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="text-muted-foreground size-6 animate-spin" />
        </div>
      ) : keys.length === 0 ? (
        <p className="text-muted-foreground py-6 text-sm">{t("noKeys")}</p>
      ) : (
        <div className="space-y-2">
          {keys.map((key) => (
            <div
              key={key.id}
              className="border-border/70 flex items-center justify-between gap-3 rounded-xl border px-3 py-3"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <KeyRound className="text-muted-foreground size-4 shrink-0" />
                  <span className="font-medium">{key.name}</span>
                  <Badge variant="secondary" className="font-mono text-xs">
                    {key.prefix}…
                  </Badge>
                </div>
                <p className="text-muted-foreground mt-1 text-xs">
                  {t("createdAt", { time: formatDateTime(key.created_at) })}
                  {key.last_used_at
                    ? ` · ${t("lastUsedAt", { time: formatDateTime(key.last_used_at) })}`
                    : ""}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => void handleRevoke(key.id)}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("createKey")}</DialogTitle>
            <DialogDescription>{t("createDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="service-key-name">{t("keyName")}</Label>
            <Input
              id="service-key-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("keyNamePlaceholder")}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button onClick={() => void handleCreate()} disabled={creating}>
              {creating && <Loader2 className="mr-1 size-4 animate-spin" />}
              {tCommon("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={createdKey != null} onOpenChange={(open) => !open && setCreatedKey(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("keyCreatedTitle")}</DialogTitle>
            <DialogDescription>{t("keyCreatedDesc")}</DialogDescription>
          </DialogHeader>
          {createdKey ? (
            <div className="space-y-3">
              <div className="bg-muted/40 rounded-lg border p-3 font-mono text-xs break-all">
                {createdKey.plaintext}
              </div>
              <Button type="button" variant="outline" onClick={() => void copyPlaintext(createdKey.plaintext)}>
                <Copy className="mr-1 size-4" />
                {t("copyKey")}
              </Button>
            </div>
          ) : null}
          <DialogFooter>
            <Button onClick={() => setCreatedKey(null)}>{tCommon("close")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
