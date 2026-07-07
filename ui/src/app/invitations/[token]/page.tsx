"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { teamApi, type TeamInvitationPreview, type TeamMember } from "@/lib/api/team";
import { ACTIVE_WORKSPACE_KEY, LEGACY_ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";
import { writeLocalStorageKey } from "@/lib/storage-migration";
import { useAuth } from "@/providers/auth-provider";

function roleLabel(role: TeamMember["role"], t: ReturnType<typeof useTranslations<"teams">>) {
  if (role === "owner") return t("roleOwner");
  if (role === "admin") return t("roleAdmin");
  return t("roleMember");
}

function oauthHref(provider: "google" | "github", token: string) {
  const query = new URLSearchParams({
    redirect: `/invitations/${token}`,
    team_invite_token: token,
  });
  return `/api/auth/oauth/${provider}/login?${query.toString()}`;
}

export default function AcceptInvitationPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const router = useRouter();
  const { user, loading, refresh } = useAuth();
  const t = useTranslations("teams");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");
  const [preview, setPreview] = useState<TeamInvitationPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [registerError, setRegisterError] = useState("");

  const loginHref = useMemo(
    () => `/login?redirect=${encodeURIComponent(`/invitations/${token}`)}`,
    [token],
  );

  const loadPreview = useCallback(async () => {
    setPreviewLoading(true);
    setPreviewError("");
    try {
      const data = await teamApi.preview(token);
      setPreview(data);
      if (data.email_hint) {
        const [localPart] = data.email_hint.split("@");
        const visible = localPart.replace(/\*+$/, "");
        if (visible) {
          setUsername(visible);
        }
      }
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : t("invitePreviewFailed"));
    } finally {
      setPreviewLoading(false);
    }
  }, [t, token]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const finishJoin = useCallback(
    (member: TeamMember) => {
      writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, member.team_id);
      setAccepted(true);
      toast.success(t("acceptSuccess"));
      router.replace("/");
    },
    [router, t],
  );

  useEffect(() => {
    if (loading || !user || accepting || accepted || !preview || preview.status !== "pending") return;
    if (preview.requires_registration) return;
    setAccepting(true);
    void teamApi
      .accept(token)
      .then(finishJoin)
      .catch((error) => {
        toast.error(error instanceof Error ? error.message : t("acceptFailed"));
      })
      .finally(() => {
        setAccepting(false);
      });
  }, [accepted, accepting, finishJoin, loading, preview, t, token, user]);

  async function onRegister(event: FormEvent) {
    event.preventDefault();
    setRegistering(true);
    setRegisterError("");
    try {
      const member = await teamApi.registerAndAccept(token, { email, username, password });
      await refresh();
      finishJoin(member);
    } catch (error) {
      setRegisterError(error instanceof Error ? error.message : tAuth("registerFailed"));
    } finally {
      setRegistering(false);
    }
  }

  if (loading || previewLoading) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }

  if (previewError || !preview) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{t("acceptInviteTitle")}</CardTitle>
            <CardDescription>{previewError || t("invitePreviewFailed")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" asChild>
              <Link href="/">{tCommon("backHome")}</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (preview.status === "accepted") {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{t("acceptInviteTitle")}</CardTitle>
            <CardDescription>{t("inviteAlreadyAccepted")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link href="/">{tCommon("backHome")}</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (preview.status === "expired") {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{t("acceptInviteTitle")}</CardTitle>
            <CardDescription>{t("inviteExpired")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" asChild>
              <Link href="/">{tCommon("backHome")}</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!user && preview.requires_registration) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{t("acceptInviteTitle")}</CardTitle>
            <CardDescription>
              {t("inviteRegisterDescription", {
                teamName: preview.team_name,
                role: roleLabel(preview.role, t),
              })}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onRegister} className="space-y-4">
              <Input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={tAuth("emailPlaceholder")}
                type="email"
                autoComplete="email"
              />
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={tAuth("usernamePlaceholder")}
                autoComplete="username"
              />
              <Input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={tAuth("passwordMinPlaceholder")}
                type="password"
                autoComplete="new-password"
              />
              {registerError ? <p className="text-destructive text-sm">{registerError}</p> : null}
              <Button className="w-full" disabled={registering}>
                {registering ? tAuth("registering") : t("registerAndJoin")}
              </Button>
              <div className="grid grid-cols-2 gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => (window.location.href = oauthHref("google", token))}
                >
                  Google
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => (window.location.href = oauthHref("github", token))}
                >
                  GitHub
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{t("acceptInviteTitle")}</CardTitle>
            <CardDescription>
              {t("inviteLoginDescription", {
                teamName: preview.team_name,
                role: roleLabel(preview.role, t),
              })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button asChild className="w-full">
              <Link href={loginHref}>{tAuth("login")}</Link>
            </Button>
            {!preview.email_hint ? (
              <p className="text-muted-foreground text-sm">{t("inviteNeedsPlatformAccess")}</p>
            ) : null}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="bg-background flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{t("acceptInviteTitle")}</CardTitle>
          <CardDescription>
            {accepting || !accepted
              ? t("acceptInviteProcessing", { teamName: preview.team_name })
              : t("acceptSuccess")}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center py-4">
          {accepting ? <Loader2 className="size-6 animate-spin" /> : null}
          {!accepting && !accepted ? (
            <Button variant="outline" asChild>
              <Link href="/">{tCommon("backHome")}</Link>
            </Button>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
