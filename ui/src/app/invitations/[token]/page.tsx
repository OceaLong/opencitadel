"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { teamApi } from "@/lib/api/team";
import { ACTIVE_WORKSPACE_KEY, LEGACY_ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";
import { writeLocalStorageKey } from "@/lib/storage-migration";
import { useAuth } from "@/providers/auth-provider";

export default function AcceptInvitationPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const router = useRouter();
  const { user, loading } = useAuth();
  const t = useTranslations("teams");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");
  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState(false);

  useEffect(() => {
    if (loading || !user || accepting || accepted) return;
    setAccepting(true);
    void teamApi
      .accept(token)
      .then((member) => {
        writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, member.team_id);
        setAccepted(true);
        toast.success(t("acceptSuccess"));
        router.replace("/");
      })
      .catch((error) => {
        toast.error(error instanceof Error ? error.message : t("acceptFailed"));
      })
      .finally(() => {
        setAccepting(false);
      });
  }, [accepted, accepting, loading, router, t, token, user]);

  if (loading) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{t("acceptInviteTitle")}</CardTitle>
            <CardDescription>{t("acceptInviteLoginRequired")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild className="w-full">
              <Link href={`/login?redirect=${encodeURIComponent(`/invitations/${token}`)}`}>
                {tAuth("login")}
              </Link>
            </Button>
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
            {accepting || !accepted ? t("acceptInviteProcessing") : t("acceptSuccess")}
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
