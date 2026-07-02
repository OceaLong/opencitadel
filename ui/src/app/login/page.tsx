"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { authApi } from "@/lib/api/auth";
import { useAuth } from "@/providers/auth-provider";

export default function LoginPage() {
  const router = useRouter();
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
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("loginFailed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="bg-background flex min-h-screen items-center justify-center p-6">
      <form onSubmit={onSubmit} className="border-border bg-card w-full max-w-sm space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <h1 className="text-xl font-semibold">{t("loginTitle")}</h1>
          <p className="text-muted-foreground mt-1 text-sm">{t("loginPageDescription")}</p>
        </div>
        <Input
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          placeholder={t("identifierPlaceholder")}
        />
        <Input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={t("passwordPlaceholder")}
          type="password"
        />
        {error && <p className="text-destructive text-sm">{error}</p>}
        <Button className="w-full" disabled={loading}>
          {loading ? t("loggingIn") : t("login")}
        </Button>
        <div className="grid grid-cols-2 gap-2">
          <Button type="button" variant="outline" onClick={() => (window.location.href = "/api/auth/oauth/google/login")}>
            Google
          </Button>
          <Button type="button" variant="outline" onClick={() => (window.location.href = "/api/auth/oauth/github/login")}>
            GitHub
          </Button>
        </div>
      </form>
    </main>
  );
}
