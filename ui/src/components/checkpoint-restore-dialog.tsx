"use client";

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
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>确认回退还原点</DialogTitle>
          <DialogDescription>
            确定要回退到「{checkpoint?.label || "此处"}」吗？将删除该点之后的所有对话、Agent
            记忆、沙箱文件与云端文件记录。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={restoring}
          >
            取消
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={restoring}>
            {restoring ? "回退中..." : "确认回退"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
