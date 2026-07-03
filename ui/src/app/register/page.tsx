"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { authApi } from "@/lib/api/auth";
import { useAuth } from "@/providers/auth-provider";

export default function RegisterPage() {
  const router = useRouter();
  const params = useSearchParams();
  const inviteToken = useMemo(() => params.get("invite_token") || "", [params]);
  const { refresh } = useAuth();
  const t = useTranslations("auth");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await authApi.register({ invite_token: inviteToken, email, username, password });
      await refresh();
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("registerFailed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="bg-background flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-[360px] shadow-card">
        <CardHeader className="items-center text-center">
          <OpenCitadelIcon variant="icon" className="mb-2 size-10" />
          <CardTitle className="text-xl">{t("registerTitle")}</CardTitle>
          <CardDescription>{t("registerDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            {!inviteToken ? (
              <p className="text-destructive text-sm">{t("missingInviteToken")}</p>
            ) : null}
            <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t("emailPlaceholder")} />
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={t("usernamePlaceholder")}
            />
            <Input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("passwordMinPlaceholder")}
              type="password"
            />
            {error ? <p className="text-destructive text-sm">{error}</p> : null}
            <Button className="w-full" disabled={loading || !inviteToken}>
              {loading ? t("registering") : t("registerAndLogin")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
