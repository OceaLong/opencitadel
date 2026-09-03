"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { useEnabledOAuthProviders } from "@/hooks/use-oauth-providers";
import { authApi } from "@/lib/api/auth";
import { resolveSafeRedirectPath } from "@/lib/safe-redirect";
import { useAuth } from "@/providers/auth-provider";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const redirectPath = useMemo(() => resolveSafeRedirectPath(params.get("redirect")), [params]);
  const { refresh } = useAuth();
  const t = useTranslations("auth");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const oauthProviders = useEnabledOAuthProviders();

  function oauthHref(provider: "google" | "github") {
    const query = new URLSearchParams();
    if (redirectPath !== "/") {
      query.set("redirect", redirectPath);
    }
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return `/api/auth/oauth/${provider}/login${suffix}`;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await authApi.login(identifier, password);
      await refresh();
      router.replace(redirectPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("loginFailed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="bg-background flex min-h-screen items-center justify-center p-6">
      <Card className="shadow-card w-full max-w-[360px]">
        <CardHeader className="items-center text-center">
          <OpenCitadelIcon variant="icon" className="mb-2 size-10" />
          <CardTitle className="text-xl">{t("loginTitle")}</CardTitle>
          <CardDescription>{t("loginPageDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="login-identifier">{t("identifierPlaceholder")}</Label>
              <Input
                id="login-identifier"
                name="identifier"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                placeholder={t("identifierPlaceholder")}
                autoComplete="username"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="login-password">{t("passwordPlaceholder")}</Label>
              <Input
                id="login-password"
                name="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("passwordPlaceholder")}
                type="password"
                autoComplete="current-password"
              />
            </div>
            {error ? <p className="text-destructive text-sm">{error}</p> : null}
            <Button className="w-full" disabled={loading}>
              {loading ? t("loggingIn") : t("login")}
            </Button>
            {(oauthProviders.has("google") || oauthProviders.has("github")) && (
              <div className="grid grid-cols-2 gap-2">
                {oauthProviders.has("google") && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => (window.location.href = oauthHref("google"))}
                  >
                    <span translate="no">Google</span>
                  </Button>
                )}
                {oauthProviders.has("github") && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => (window.location.href = oauthHref("github"))}
                  >
                    <span translate="no">GitHub</span>
                  </Button>
                )}
              </div>
            )}
            {/* 注册需邀请：无邀请 token 场景不提供注册链接，避免链到永久禁用的注册页。 */}
            <p className="text-muted-foreground text-center text-xs">
              {t("inviteOnlyRegistration")}
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
