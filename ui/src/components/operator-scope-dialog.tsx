"use client";

import { useState } from "react";
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>目标系统归属声明</DialogTitle>
          <DialogDescription>
            Web Operator 仅适用于企业自有或自建系统。请选择本次任务的目标系统归属，该声明将写入审计留痕。
          </DialogDescription>
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
              <span className="text-sm font-medium">企业自有 / 自建系统</span>
              {scope === "owned" && <Check className="text-primary size-4" />}
            </div>
            <p className="text-muted-foreground mt-1 text-xs">
              您有权操作的目标系统（内网后台、自建演示环境等）。
            </p>
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
              <span className="text-sm font-medium">第三方 SaaS</span>
              {scope === "third_party_saas" && <Check className="size-4 text-amber-700" />}
            </div>
            <p className="text-muted-foreground mt-1 flex items-start gap-1 text-xs">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
              可能违反服务条款或触发未授权访问风险；仅建议在明确授权场景下使用，操作将完整审计。
            </p>
          </button>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => {
              onConfirm(scope);
              onOpenChange(false);
            }}
          >
            确认并创建任务
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
