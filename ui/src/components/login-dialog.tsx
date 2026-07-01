"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import { authApi } from "@/lib/api/auth";
import { useAuth } from "@/providers/auth-provider";

type LoginDialogProps = {
  open: boolean;
  reason?: string | null;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
};

export function LoginDialog({ open, reason, onOpenChange, onSuccess }: LoginDialogProps) {
  const { refresh } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await authApi.login(identifier, password);
      await refresh();
      setIdentifier("");
      setPassword("");
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>登录 MyManus</DialogTitle>
          <DialogDescription>
            {reason ?? "登录后即可使用 AI 对话、代码知识库、文档问答等智能功能。"}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <Input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="邮箱或用户名"
            autoComplete="username"
          />
          <Input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="密码"
            type="password"
            autoComplete="current-password"
          />
          {error && <p className="text-destructive text-sm">{error}</p>}
          <Button className="w-full" disabled={loading}>
            {loading ? "登录中..." : "登录"}
          </Button>
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => (window.location.href = "/api/auth/oauth/google/login")}
            >
              Google
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => (window.location.href = "/api/auth/oauth/github/login")}
            >
              GitHub
            </Button>
          </div>
          <p className="text-muted-foreground text-center text-xs">
            还没有账号？{" "}
            <Link href="/register" className="text-primary underline underline-offset-4">
              使用邀请链接注册
            </Link>
          </p>
        </form>
      </DialogContent>
    </Dialog>
  );
}
