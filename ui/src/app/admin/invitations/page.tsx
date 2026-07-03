"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { formatDateTime } from "@/lib/admin-utils";
import { adminApi, type PlatformInvitation } from "@/lib/api/admin";
import { IconCopy, IconInvitation } from "@/lib/icons";

export default function AdminInvitationsPage() {
  const t = useTranslations("admin");
  const [invitations, setInvitations] = useState<PlatformInvitation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");
  const [creating, setCreating] = useState(false);

  async function loadInvitations() {
    setLoading(true);
    try {
      const data = await adminApi.invitations({ limit: 100 });
      setInvitations(data.invitations);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadInvitations();
  }, []);

  async function createInvite() {
    if (!inviteEmail.trim()) {
      toast.error(t("enterEmail"));
      return;
    }
    setCreating(true);
    try {
      const data = await adminApi.invite(inviteEmail.trim());
      setInviteUrl(data.url);
      toast.success(t("inviteLinkGenerated"));
      setInviteEmail("");
      await loadInvitations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("generateFailed"));
    } finally {
      setCreating(false);
    }
  }

  async function copyUrl(url: string) {
    await navigator.clipboard.writeText(url);
    toast.success(t("inviteLinkCopied"));
  }

  const pending = invitations.filter((item) => item.status === "pending").length;
  const accepted = invitations.filter((item) => item.status === "accepted").length;
  const expired = invitations.filter((item) => item.status === "expired").length;

  return (
    <div className="space-y-6">
      <PageHeader
        bordered={false}
        title={t("platformInvite")}
        description={t("invitationsSubtitle")}
      />

      <div className="grid gap-3 md:grid-cols-3">
        <SummaryCard label={t("summaryPending")} value={pending} />
        <SummaryCard label={t("summaryAccepted")} value={accepted} />
        <SummaryCard label={t("summaryExpired")} value={expired} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("generateInviteTitle")}</CardTitle>
          <CardDescription>{t("inviteValidDays")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              placeholder="user@example.com"
            />
            <Button disabled={creating} onClick={() => void createInvite()}>
              {creating ? <LoadingSpinner /> : <IconInvitation className="mr-1 size-4" />}
              {t("generateInviteLink")}
            </Button>
          </div>
          {inviteUrl ? (
            <div className="bg-muted/30 flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
              <span className="truncate">{inviteUrl}</span>
              <Button variant="ghost" size="icon" onClick={() => void copyUrl(inviteUrl)}>
                <IconCopy className="size-4" />
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("invitationRecordsTitle")}</CardTitle>
          <CardDescription>{t("totalRecords", { count: total })}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : invitations.length === 0 ? (
            <div className="text-muted-foreground py-10 text-center text-sm">{t("noInvitationRecords")}</div>
          ) : (
            <div className="space-y-2">
              {invitations.map((item) => (
                <div key={item.id} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-3">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{item.email || t("noEmailSpecified")}</div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      {t("createdAt", { time: formatDateTime(item.created_at) })} · {t("expiresAt", { time: formatDateTime(item.expires_at) })}
                    </div>
                  </div>
                  <StatusBadge status={item.status} />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <Card className="gap-0 py-4">
      <CardContent>
        <div className="text-muted-foreground text-sm">{label}</div>
        <div className="mt-1 text-2xl font-semibold">{value}</div>
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: PlatformInvitation["status"] }) {
  const t = useTranslations("admin");
  const label =
    status === "accepted" ? t("inviteAccepted") : status === "pending" ? t("invitePending") : t("inviteExpired");
  const variant = status === "accepted" ? "secondary" : status === "pending" ? "outline" : "destructive";
  return <Badge variant={variant}>{label}</Badge>;
}
