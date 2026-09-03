"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";

export type RecycleBinItem = {
  id: string;
  primary: string;
  secondary?: string;
};

type RecycleBinDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 对话框标题（会话/知识库各自传入） */
  title: string;
  /** 对话框副标题说明 */
  description?: string;
  /** 拉取回收站列表 */
  load: () => Promise<RecycleBinItem[]>;
  /** 恢复一项 */
  restore: (id: string) => Promise<void>;
  /** 彻底删除一项（需二次确认） */
  purge: (id: string) => Promise<void>;
  /** 恢复/彻底删除成功后回调，供父级刷新自身列表 */
  onChanged?: () => void;
};

/**
 * 通用回收站对话框：展示已软删除项，支持恢复与彻底删除（彻底删除需二次确认）。
 *
 * 与具体资源解耦——会话与知识库分别注入各自的 load/restore/purge。
 */
export function RecycleBinDialog({
  open,
  onOpenChange,
  title,
  description,
  load,
  restore,
  purge,
  onChanged,
}: RecycleBinDialogProps) {
  const t = useTranslations("recycleBin");
  const [items, setItems] = useState<RecycleBinItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [pendingPurge, setPendingPurge] = useState<RecycleBinItem | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await load());
    } catch {
      toast.error(t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [load, t]);

  // The open/close effect intentionally depends only on `open` and reads
  // `refresh` through a ref. Depending on `refresh` directly would re-run this
  // effect (and call setItems) on every render whenever the injected `load`/`t`
  // identities are unstable, which spins into an infinite render loop.
  const refreshRef = useRef(refresh);
  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    if (open) void refreshRef.current();
    else setItems([]);
  }, [open]);

  const handleRestore = async (item: RecycleBinItem) => {
    setActioningId(item.id);
    try {
      await restore(item.id);
      setItems((prev) => prev.filter((it) => it.id !== item.id));
      toast.success(t("restoreSuccess", { name: item.primary }));
      onChanged?.();
    } catch {
      toast.error(t("restoreFailed"));
    } finally {
      setActioningId(null);
    }
  };

  const handlePurgeConfirm = async () => {
    if (!pendingPurge) return;
    const item = pendingPurge;
    setActioningId(item.id);
    try {
      await purge(item.id);
      setItems((prev) => prev.filter((it) => it.id !== item.id));
      toast.success(t("purgeSuccess", { name: item.primary }));
      onChanged?.();
    } catch {
      toast.error(t("purgeFailed"));
    } finally {
      setActioningId(null);
      setPendingPurge(null);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>

          {loading ? (
            <div className="text-muted-foreground flex items-center justify-center gap-2 py-10">
              <Loader2 className="size-4 animate-spin" />
              {t("loading")}
            </div>
          ) : items.length === 0 ? (
            <EmptyState title={t("empty")} className="py-10" />
          ) : (
            <ScrollArea className="max-h-[60vh]">
              <ul className="space-y-1 pr-2">
                {items.map((item) => (
                  <li
                    key={item.id}
                    className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium" title={item.primary}>
                        {item.primary}
                      </p>
                      {item.secondary ? (
                        <p
                          className="text-muted-foreground truncate text-xs"
                          title={item.secondary}
                        >
                          {item.secondary}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={actioningId === item.id}
                        onClick={() => void handleRestore(item)}
                      >
                        {actioningId === item.id ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <RotateCcw className="size-4" />
                        )}
                        {t("restore")}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("purge")}
                        className="text-destructive hover:text-destructive"
                        disabled={actioningId === item.id}
                        onClick={() => setPendingPurge(item)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </ScrollArea>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDeleteDialog
        open={pendingPurge != null}
        onOpenChange={(isOpen) => !isOpen && setPendingPurge(null)}
        title={t("purgeConfirmTitle")}
        description={t("purgeConfirmDescription", { name: pendingPurge?.primary ?? "" })}
        confirmLabel={t("purge")}
        onConfirm={handlePurgeConfirm}
      />
    </>
  );
}
