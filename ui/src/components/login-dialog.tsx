"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

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
  const t = useTranslations("auth");
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
      setError(err instanceof Error ? err.message : t("loginFailed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("loginTitle")}</DialogTitle>
          <DialogDescription>
            {reason ?? t("loginDescription")}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <Input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder={t("identifierPlaceholder")}
            autoComplete="username"
          />
          <Input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("passwordPlaceholder")}
            type="password"
            autoComplete="current-password"
          />
          {error && <p className="text-destructive text-sm">{error}</p>}
          <Button className="w-full" disabled={loading}>
            {loading ? t("loggingIn") : t("login")}
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
            {t("noAccount")}{" "}
            <Link href="/register" className="text-primary underline underline-offset-4">
              {t("registerLink")}
            </Link>
          </p>
        </form>
      </DialogContent>
    </Dialog>
  );
}
