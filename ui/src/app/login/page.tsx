"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
      <Card className="w-full max-w-[360px] shadow-card">
        <CardHeader className="items-center text-center">
          <OpenCitadelIcon variant="icon" className="mb-2 size-10" />
          <CardTitle className="text-xl">{t("loginTitle")}</CardTitle>
          <CardDescription>{t("loginPageDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
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
            {error ? <p className="text-destructive text-sm">{error}</p> : null}
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
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
